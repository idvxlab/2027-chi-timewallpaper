from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional, Tuple
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from app.core.config import settings


Region = Literal["upper", "lower"]
ViewerRole = Literal["child", "elder"]
PersonRole = Literal["young", "elder"]

ROI_BBOX_NORM: dict[Region, Tuple[float, float, float, float]] = {
    # The child generation anchor is intentionally inset from the right edge.
    # Start at x=0.52 so the shifted hair and shoulder remain inside the crop,
    # while still excluding most of the connected central terrace.
    "upper": (0.52, 0.00, 1.00, 0.65),
    "lower": (0.00, 0.35, 0.65, 1.00),
}
BBOX_PAD_PX = 24


def resolve_person_role(_viewer_role: ViewerRole, region: Region) -> PersonRole:
    """Resolve the fixed shared-wallpaper occupant, independent of viewer."""

    return "young" if region == "upper" else "elder"


@dataclass
class ExtractResult:
    cutout_path: Path
    cutout_url: str
    bbox: dict[str, int]
    method: str


_SESSION = None
_SESSION_MODEL = "u2netp"
_SESSION_LOAD_ERROR: Optional[str] = None


def _get_session():
    global _SESSION, _SESSION_LOAD_ERROR
    if _SESSION is not None:
        return _SESSION
    if _SESSION_LOAD_ERROR is not None:
        return None
    try:
        from rembg import new_session  # type: ignore

        started = time.time()
        _SESSION = new_session(
            _SESSION_MODEL,
            providers=["CPUExecutionProvider"],
        )
        print(
            f"[subject.extract] rembg model={_SESSION_MODEL} "
            f"loaded in {time.time() - started:.2f}s"
        )
        return _SESSION
    except Exception as exc:  # noqa: BLE001
        _SESSION_LOAD_ERROR = repr(exc)
        print(f"[subject.extract] rembg unavailable: {exc!r}; using fallback")
        return None


def _generated_dir() -> Path:
    path = (Path(settings.storage_local_dir) / "generated").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_image_path(image_url: str) -> Path:
    if not image_url:
        raise ValueError("imageUrl is empty")

    url_path = unquote(urlsplit(image_url).path)
    marker = "/generated/"
    if marker not in url_path:
        raise ValueError("Only locally generated wallpaper URLs are supported")

    relative = url_path.split(marker, 1)[1]
    base = _generated_dir()
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError("Invalid generated wallpaper path") from exc
    return candidate


def _region_bbox_px(image: Image.Image, region: Region) -> Tuple[int, int, int, int]:
    nx0, ny0, nx1, ny1 = ROI_BBOX_NORM[region]
    width, height = image.size
    x0 = max(0, min(width, round(nx0 * width)))
    y0 = max(0, min(height, round(ny0 * height)))
    x1 = max(0, min(width, round(nx1 * width)))
    y1 = max(0, min(height, round(ny1 * height)))
    return x0, y0, max(x1, x0 + 1), max(y1, y0 + 1)


def _largest_connected_component(mask: Any) -> Any:
    import numpy as np

    height, width = mask.shape
    visited = np.zeros((height, width), dtype=bool)
    labels = np.zeros((height, width), dtype=np.int32)
    best_label = 0
    best_size = 0
    label = 1

    for start_y in range(height):
        for start_x in range(width):
            if mask[start_y, start_x] == 0 or visited[start_y, start_x]:
                continue
            stack = [(start_y, start_x)]
            visited[start_y, start_x] = True
            size = 0
            while stack:
                y, x = stack.pop()
                labels[y, x] = label
                size += 1
                for next_y, next_x in (
                    (y - 1, x),
                    (y + 1, x),
                    (y, x - 1),
                    (y, x + 1),
                ):
                    if (
                        0 <= next_y < height
                        and 0 <= next_x < width
                        and mask[next_y, next_x]
                        and not visited[next_y, next_x]
                    ):
                        visited[next_y, next_x] = True
                        stack.append((next_y, next_x))
            if size > best_size:
                best_label = label
                best_size = size
            label += 1

    if best_label == 0:
        return np.zeros_like(mask)
    return (labels == best_label).astype(np.uint8) * 255


def _tighten_mask(mask: Any, threshold: int = 16) -> Any:
    import numpy as np

    binary = (mask >= threshold).astype(np.uint8) * 255
    return _largest_connected_component(binary) if binary.any() else binary


def _run_rembg(image: Image.Image) -> Optional[Any]:
    session = _get_session()
    if session is None:
        return None
    try:
        import numpy as np
        from rembg import remove  # type: ignore

        started = time.time()
        output = remove(image, session=session, only_mask=True)
        if output.mode != "L":
            output = output.convert("L")
        print(
            f"[subject.extract] segmentation size={image.size} "
            f"elapsed={time.time() - started:.2f}s"
        )
        return np.array(output)
    except Exception as exc:  # noqa: BLE001
        print(f"[subject.extract] segmentation failed: {exc!r}; using fallback")
        return None


def _add_white_outline(rgba: Image.Image, outline_px: int = 9) -> Image.Image:
    rgba = rgba.convert("RGBA")
    alpha = rgba.getchannel("A")
    dilated = alpha.filter(ImageFilter.MaxFilter(outline_px * 2 + 1))
    outline_alpha = ImageChops.subtract(dilated, alpha)
    outline = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    outline.putalpha(outline_alpha)
    return Image.alpha_composite(outline, rgba)


def _save_cutout(image: Image.Image) -> tuple[Path, str]:
    output_name = f"cutout-{uuid.uuid4().hex}.png"
    output_path = _generated_dir() / output_name
    image.save(output_path, format="PNG", optimize=True)
    output_url = f"{settings.public_api_base_url.rstrip('/')}/generated/{output_name}"
    return output_path, output_url


def _soft_crop_fallback(
    *,
    source: Image.Image,
    bbox: Tuple[int, int, int, int],
) -> ExtractResult:
    x0, y0, x1, y1 = bbox
    crop = source.crop(bbox).convert("RGBA")
    width, height = crop.size
    mask = Image.new("L", crop.size, 0)
    draw = ImageDraw.Draw(mask)
    inset_x = int(width * 0.08)
    inset_y = int(height * 0.08)
    draw.ellipse(
        (inset_x, inset_y, width - inset_x, height - inset_y),
        fill=255,
    )
    mask = mask.filter(
        ImageFilter.GaussianBlur(radius=max(8, min(width, height) // 14))
    )
    red, green, blue, alpha = crop.split()
    rgba = Image.merge("RGBA", (red, green, blue, ImageChops.darker(alpha, mask)))
    output_path, output_url = _save_cutout(_add_white_outline(rgba))
    return ExtractResult(
        cutout_path=output_path,
        cutout_url=output_url,
        bbox={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
        method="soft_crop_fallback",
    )


def extract_subject_from_wallpaper(
    *,
    image_url: str,
    point_x: float,
    point_y: float,
    region: Region,
    viewer_role: ViewerRole,
) -> ExtractResult:
    person_role = resolve_person_role(viewer_role, region)
    print(
        f"[subject.extract] region={region} viewerRole={viewer_role} "
        f"personRole={person_role} point=({point_x:.3f},{point_y:.3f})"
    )

    source_path = _resolve_image_path(image_url)
    if not source_path.is_file():
        raise FileNotFoundError(f"Source wallpaper was not found: {source_path.name}")

    with Image.open(source_path) as source_image:
        source = source_image.convert("RGB")
        image_width, image_height = source.size
        roi_bbox = _region_bbox_px(source, region)
        roi = source.crop(roi_bbox)
        mask_array = _run_rembg(roi)
        if mask_array is None:
            return _soft_crop_fallback(source=source, bbox=roi_bbox)

        import numpy as np

        binary = _tighten_mask(mask_array)
        coordinates_y, coordinates_x = np.where(binary > 0)
        if len(coordinates_y) == 0:
            return _soft_crop_fallback(source=source, bbox=roi_bbox)

        roi_x0, roi_y0, roi_x1, roi_y1 = roi_bbox
        mask_x0 = int(coordinates_x.min())
        mask_x1 = int(coordinates_x.max()) + 1
        mask_y0 = int(coordinates_y.min())
        mask_y1 = int(coordinates_y.max()) + 1

        global_x0 = max(roi_x0, roi_x0 + mask_x0 - BBOX_PAD_PX)
        global_y0 = max(roi_y0, roi_y0 + mask_y0 - BBOX_PAD_PX)
        global_x1 = min(roi_x1, roi_x0 + mask_x1 + BBOX_PAD_PX)
        global_y1 = min(roi_y1, roi_y0 + mask_y1 + BBOX_PAD_PX)
        global_x1 = min(global_x1, image_width)
        global_y1 = min(global_y1, image_height)

        crop = source.crop((global_x0, global_y0, global_x1, global_y1))
        slice_x0 = global_x0 - roi_x0
        slice_y0 = global_y0 - roi_y0
        slice_x1 = slice_x0 + crop.width
        slice_y1 = slice_y0 + crop.height
        mask_slice = binary[slice_y0:slice_y1, slice_x0:slice_x1]
        if mask_slice.shape != (crop.height, crop.width):
            padded = np.zeros((crop.height, crop.width), dtype=np.uint8)
            copy_height = min(crop.height, mask_slice.shape[0])
            copy_width = min(crop.width, mask_slice.shape[1])
            padded[:copy_height, :copy_width] = mask_slice[:copy_height, :copy_width]
            mask_slice = padded

        alpha = Image.fromarray(mask_slice, mode="L").filter(
            ImageFilter.GaussianBlur(radius=3)
        )
        red, green, blue = crop.split()
        rgba = Image.merge("RGBA", (red, green, blue, alpha))
        output_path, output_url = _save_cutout(_add_white_outline(rgba))
        return ExtractResult(
            cutout_path=output_path,
            cutout_url=output_url,
            bbox={
                "x": global_x0,
                "y": global_y0,
                "width": global_x1 - global_x0,
                "height": global_y1 - global_y0,
            },
            method="segmentation",
        )
