"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import Image from "next/image";
import {
  createOnboardingProfile,
  getCurrentRelationshipCharacterAssets,
  resolveApiAssetUrl,
  uploadMyCharacterAsset,
  type CharacterAsset,
  type OnboardingProfileResult,
  type RelationshipCharacterAssets,
} from "@/lib/api";
import {
  useOnboardingStore,
  type OnboardingRole,
  type OnboardingUserContext,
} from "@/lib/hooks/useOnboardingStore";
import { subscribeToCharacterAssetEvents } from "@/lib/characterAssetEvents";
import { ProfileSettingsScreen } from "@/components/onboarding/ProfileSettingsScreen";
import { useI18n } from "@/lib/i18n";

type UploadSource = "camera" | "album";

const TEMPORARY_PROFILE_NAMES = new Set(["Older Adult", "Adult Child"]);

function ProgressIndicator() {
  const { t } = useI18n();
  return (
    <div
      aria-label={t("photo.stepLabel")}
      className="absolute bottom-[clamp(30px,4.8vh,58px)] left-1/2 z-30 flex -translate-x-1/2 items-center gap-[44px]"
    >
      <span className="h-[6px] w-[88px] rounded-full bg-black" />
      <span className="h-[6px] w-[88px] rounded-full bg-black" />
      <span className="h-[6px] w-[88px] rounded-full bg-[#d8d7d4]" />
    </div>
  );
}

function UploadSourcePicker({
  visible,
  onSelect,
  onClose,
}: {
  visible: boolean;
  onSelect: (source: UploadSource) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  if (!visible) return null;

  return (
    <div className="absolute inset-0 z-[70]">
      <button
        type="button"
        aria-label={t("photo.closePicker")}
        onClick={onClose}
        className="absolute inset-0 h-full w-full cursor-default bg-black/10"
      />

      <section
        role="dialog"
        aria-modal="true"
        aria-label={t("photo.chooseSource")}
        className="absolute inset-x-0 bottom-0 rounded-t-[30px] bg-white px-[clamp(26px,6vw,54px)] pb-[clamp(34px,5vh,58px)] pt-6 shadow-[0_-16px_42px_rgba(42,35,28,0.16)]"
      >
        <div className="mx-auto h-[5px] w-[54px] rounded-full bg-[#d9d9d9]" />
        <h2 className="mt-5 text-center text-[clamp(24px,4.2vw,32px)] font-bold text-black">
          {t("photo.chooseSource")}
        </h2>

        <div className="mt-6 flex flex-col gap-4">
          <button
            type="button"
            onClick={() => onSelect("camera")}
            className="flex min-h-[84px] w-full items-center gap-5 rounded-[18px] border border-black/15 bg-white px-5 py-4 text-left transition hover:-translate-y-0.5 hover:border-black/40 hover:bg-[#faf8f3] focus:outline-none focus-visible:ring-2 focus-visible:ring-black"
          >
            <span className="flex h-[54px] w-[54px] shrink-0 items-center justify-center rounded-[14px] bg-[#eee7da]">
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="h-7 w-7 text-black"
              >
                <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
                <circle cx="12" cy="13" r="4" />
              </svg>
            </span>
            <span>
              <span className="block text-[clamp(22px,3.8vw,29px)] font-bold leading-tight text-black">
                {t("photo.takePhoto")}
              </span>
              <span className="mt-1 block text-[clamp(16px,2.6vw,20px)] font-medium text-black/50">
                {t("photo.useCamera")}
              </span>
            </span>
          </button>

          <button
            type="button"
            onClick={() => onSelect("album")}
            className="flex min-h-[84px] w-full items-center gap-5 rounded-[18px] border border-black/15 bg-white px-5 py-4 text-left transition hover:-translate-y-0.5 hover:border-black/40 hover:bg-[#faf8f3] focus:outline-none focus-visible:ring-2 focus-visible:ring-black"
          >
            <span className="flex h-[54px] w-[54px] shrink-0 items-center justify-center rounded-[14px] bg-[#eee7da]">
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="h-7 w-7 text-black"
              >
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <circle cx="8.5" cy="8.5" r="1.5" />
                <path d="m21 15-5-5L5 21" />
              </svg>
            </span>
            <span>
              <span className="block text-[clamp(22px,3.8vw,29px)] font-bold leading-tight text-black">
                {t("photo.uploadDevice")}
              </span>
              <span className="mt-1 block text-[clamp(16px,2.6vw,20px)] font-medium text-black/50">
                {t("photo.chooseFiles")}
              </span>
            </span>
          </button>
        </div>
      </section>
    </div>
  );
}

function HiddenPhotoInput({
  inputRef,
  capture,
  onFile,
}: {
  inputRef: RefObject<HTMLInputElement>;
  capture?: "user";
  onFile: (file: File) => void;
}) {
  return (
    <input
      ref={inputRef}
      type="file"
      accept="image/*"
      capture={capture}
      className="sr-only"
      onChange={(event) => {
        const file = event.target.files?.[0];
        if (file) onFile(file);
        event.target.value = "";
      }}
    />
  );
}

function toUserContext(
  profile: OnboardingProfileResult,
): OnboardingUserContext {
  return {
    userId: profile.userId,
    counterpartUserId: profile.counterpartUserId,
    relationshipId: profile.relationshipId,
    relationshipDisplayName: profile.relationshipDisplayName,
    inviteCode: profile.inviteCode,
    relationshipStatus: profile.relationshipStatus,
    viewerRole: profile.viewerRole,
    counterpartRole: profile.counterpartRole,
    familyRole: profile.familyRole,
  };
}

function assetDisplayUrl(asset: CharacterAsset | null): string {
  if (!asset) return "";

  const path =
    asset.status === "ready"
      ? asset.portraitImageUrl ||
        asset.masterImageUrl ||
        asset.halfBodyImageUrl ||
        asset.sourceImageUrl
      : asset.sourceImageUrl;

  return resolveApiAssetUrl(path);
}

function Portrait({
  imageUrl,
  isGenerating,
  onClick,
}: {
  imageUrl: string;
  isGenerating: boolean;
  onClick: () => void;
}) {
  const { t } = useI18n();
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isGenerating}
      aria-label={
        imageUrl ? t("photo.changePhoto") : t("photo.addPhoto")
      }
      className="relative flex h-[clamp(220px,42vw,280px)] w-[clamp(220px,42vw,280px)] items-center justify-center overflow-hidden rounded-full border border-black/20 bg-[#aaa6a6] shadow-[0_14px_28px_rgba(61,53,44,0.11)] transition hover:-translate-y-0.5 focus:outline-none focus-visible:ring-3 focus-visible:ring-black disabled:cursor-wait disabled:hover:translate-y-0"
    >
      {imageUrl ? (
        <Image
          src={imageUrl}
          alt="Your portrait"
          fill
          unoptimized
          sizes="280px"
          className="object-cover"
        />
      ) : (
        <span aria-hidden="true" className="relative h-[66px] w-[66px]">
          <span className="absolute left-1/2 top-0 h-full w-[5px] -translate-x-1/2 rounded-full bg-black" />
          <span className="absolute left-0 top-1/2 h-[5px] w-full -translate-y-1/2 rounded-full bg-black" />
        </span>
      )}

      {isGenerating ? (
        <span className="absolute inset-0 flex items-center justify-center bg-black/36">
          <span className="h-12 w-12 animate-spin rounded-full border-[5px] border-white/35 border-t-white" />
        </span>
      ) : null}
    </button>
  );
}

function NextButton({
  disabled,
  isLoading,
  onClick,
}: {
  disabled: boolean;
  isLoading: boolean;
  onClick: () => void;
}) {
  const { t } = useI18n();
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      aria-label={isLoading ? t("photo.continueLoading") : t("photo.continue")}
      className={[
        "absolute bottom-[clamp(112px,14vh,158px)] left-1/2 z-30 flex h-[82px] w-[82px] -translate-x-1/2 items-center justify-center rounded-full",
        "text-white shadow-[0_13px_25px_rgba(42,37,32,0.16)] transition duration-200",
        "focus:outline-none focus-visible:ring-3 focus-visible:ring-black focus-visible:ring-offset-3 focus-visible:ring-offset-[#eee7da]",
        disabled
          ? "cursor-not-allowed bg-black/38"
          : "cursor-pointer bg-black hover:-translate-x-1/2 hover:-translate-y-1 active:translate-y-0",
      ].join(" ")}
    >
      {isLoading ? (
        <span className="h-7 w-7 animate-spin rounded-full border-[3px] border-white/35 border-t-white" />
      ) : (
        <svg
          aria-hidden="true"
          viewBox="0 0 52 24"
          fill="none"
          className="h-8 w-[48px]"
        >
          <path
            d="M2 12h44M35 2l11 10-11 10"
            stroke="currentColor"
            strokeWidth="2.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </button>
  );
}

function CornerControls({
  onBack,
  onOpenSettings,
  disabled = false,
}: {
  onBack: () => void;
  onOpenSettings: () => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const buttonClass =
    "absolute top-[clamp(18px,3vh,30px)] z-50 flex h-[58px] w-[58px] items-center justify-center rounded-full text-black transition hover:bg-black/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-black disabled:cursor-not-allowed disabled:opacity-35";

  return (
    <>
      <button
        type="button"
        onClick={onBack}
        disabled={disabled}
        aria-label={t("photo.backLabel")}
        className={`${buttonClass} left-[clamp(18px,4vw,34px)]`}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          className="h-9 w-9"
        >
          <path
            d="m15.5 4.5-7.5 7.5 7.5 7.5"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      <button
        type="button"
        onClick={onOpenSettings}
        disabled={disabled}
        aria-label={t("photo.openSettings")}
        className={`${buttonClass} right-[clamp(18px,4vw,34px)]`}
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" className="h-8 w-8">
          <g fill="currentColor">
            <circle cx="12" cy="12" r="7" />
            {Array.from({ length: 8 }, (_, index) => (
              <rect
                key={index}
                x="10.1"
                y="1.25"
                width="3.8"
                height="5.2"
                rx="0.55"
                transform={`rotate(${index * 45} 12 12)`}
              />
            ))}
          </g>
          <circle cx="12" cy="12" r="3.2" fill="#eee7da" />
        </svg>
      </button>
    </>
  );
}

export function PhotoUploadScreen() {
  const { t } = useI18n();
  const role = useOnboardingStore((state) => state.role);
  const storedName = useOnboardingStore((state) => state.name);
  const gender = useOnboardingStore((state) => state.gender);
  const userContext = useOnboardingStore((state) => state.userContext);
  const setName = useOnboardingStore((state) => state.setName);
  const setUserContext = useOnboardingStore((state) => state.setUserContext);
  const setStep = useOnboardingStore((state) => state.setStep);
  const setSelfPhotoUrl = useOnboardingStore((state) => state.setSelfPhotoUrl);
  const setPartnerPhotoUrl = useOnboardingStore(
    (state) => state.setPartnerPhotoUrl,
  );
  const setSelfPhotoFile = useOnboardingStore(
    (state) => state.setSelfPhotoFile,
  );

  const effectiveRole: Exclude<OnboardingRole, null> = role ?? "child";
  const partnerRole: Exclude<OnboardingRole, null> =
    effectiveRole === "elder" ? "child" : "elder";

  const initialName = TEMPORARY_PROFILE_NAMES.has(storedName.trim())
    ? ""
    : storedName;

  const [preferredName, setPreferredName] = useState(initialName);
  const [assets, setAssets] = useState<RelationshipCharacterAssets | null>(
    null,
  );
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [localPreview, setLocalPreview] = useState<string | null>(null);
  const [submittedAssetId, setSubmittedAssetId] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  const cameraRef = useRef<HTMLInputElement>(null);
  const albumRef = useRef<HTMLInputElement>(null);

  const selfAsset = assets?.[effectiveRole] ?? null;
  const partnerAsset = assets?.[partnerRole] ?? null;
  const remoteSelfUrl = assetDisplayUrl(selfAsset);
  const remotePartnerUrl = assetDisplayUrl(partnerAsset);
  const portraitUrl = pendingFile ? (localPreview ?? "") : remoteSelfUrl;
  const trimmedName = preferredName.trim();
  const selfIsProcessing = selfAsset?.status === "processing";
  const isPortraitGenerating = isSubmitting || selfIsProcessing;
  const selfIsReady = selfAsset?.status === "ready" && !pendingFile;
  const partnerIsReady = partnerAsset?.status === "ready";

  const refreshAssets = useCallback(async () => {
    try {
      const result = await getCurrentRelationshipCharacterAssets();
      setAssets(result);
      setStatusError(null);
      return result;
    } catch (error) {
      console.error("[PhotoUpload] relationship asset refresh failed", error);
      setStatusError(t("photo.errorRefresh"));
      return null;
    }
  }, []);

  useEffect(() => {
    let active = true;

    const refreshWhenActive = () => {
      if (active && document.visibilityState !== "hidden") {
        void refreshAssets();
      }
    };

    void refreshAssets();
    const unsubscribe = subscribeToCharacterAssetEvents(refreshWhenActive);
    document.addEventListener("visibilitychange", refreshWhenActive);

    return () => {
      active = false;
      unsubscribe();
      document.removeEventListener("visibilitychange", refreshWhenActive);
    };
  }, [refreshAssets]);

  useEffect(() => {
    if (remoteSelfUrl && !pendingFile) setSelfPhotoUrl(remoteSelfUrl);
    if (remotePartnerUrl) setPartnerPhotoUrl(remotePartnerUrl);
  }, [
    pendingFile,
    remotePartnerUrl,
    remoteSelfUrl,
    setPartnerPhotoUrl,
    setSelfPhotoUrl,
  ]);

  useEffect(() => {
    if (
      submittedAssetId &&
      selfAsset?.assetId === submittedAssetId &&
      selfAsset.status === "ready"
    ) {
      setPendingFile(null);
      setSubmittedAssetId(null);
      setLocalPreview(null);
    }
  }, [selfAsset, submittedAssetId]);

  useEffect(() => {
    return () => {
      if (localPreview?.startsWith("blob:")) {
        URL.revokeObjectURL(localPreview);
      }
    };
  }, [localPreview]);

  const openPicker = () => {
    if (!isPortraitGenerating) setPickerOpen(true);
  };

  const selectUploadSource = (source: UploadSource) => {
    setPickerOpen(false);
    if (source === "camera") {
      cameraRef.current?.click();
    } else {
      albumRef.current?.click();
    }
  };

  const handleFileSelected = (file: File) => {
    if (localPreview?.startsWith("blob:")) {
      URL.revokeObjectURL(localPreview);
    }

    const previewUrl = URL.createObjectURL(file);
    setPendingFile(file);
    setSubmittedAssetId(null);
    setLocalPreview(previewUrl);
    setSelfPhotoFile(file);
    setSelfPhotoUrl(previewUrl);
    setUploadError(null);
    setStatusError(null);
  };

  const updatePreferredName = (value: string) => {
    setPreferredName(value);
    setName(value);
    setUploadError(null);
  };

  const savePreferredName = async () => {
    if (!trimmedName) {
      throw new Error(t("photo.errorName"));
    }
    if (!userContext?.userId || !userContext.relationshipId) {
      throw new Error(t("photo.errorSession"));
    }

    const effectiveGender = gender === "male" ? "male" : "female";
    const profile = await createOnboardingProfile({
      viewerRole: effectiveRole,
      displayName: trimmedName,
      gender: effectiveGender,
      userId: userContext.userId,
      relationshipId: userContext.relationshipId,
    });

    setName(trimmedName);
    setPreferredName(trimmedName);
    setUserContext(toUserContext(profile));
  };

  const generatePortrait = async () => {
    if (!pendingFile || !trimmedName || isPortraitGenerating) return;

    setIsSubmitting(true);
    setUploadError(null);
    setStatusError(null);

    try {
      await savePreferredName();
      const uploadedAsset = await uploadMyCharacterAsset(pendingFile);
      setSubmittedAssetId(uploadedAsset.assetId);
      setAssets((current) => {
        const counterpart = current?.[partnerRole] ?? null;
        const next: RelationshipCharacterAssets = {
          relationshipId:
            current?.relationshipId ?? userContext?.relationshipId ?? "",
          elder:
            effectiveRole === "elder"
              ? uploadedAsset
              : (current?.elder ?? null),
          child:
            effectiveRole === "child"
              ? uploadedAsset
              : (current?.child ?? null),
          ready:
            uploadedAsset.status === "ready" && counterpart?.status === "ready",
        };
        return next;
      });
      await refreshAssets();
    } catch (error) {
      console.error("[PhotoUpload] character generation failed", error);
      setSubmittedAssetId(null);
      setUploadError(
        error instanceof Error
          ? error.message
          : t("photo.errorGenerate"),
      );
      await refreshAssets();
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleGenerateButton = () => {
    if (isPortraitGenerating) return;
    if (!pendingFile) {
      openPicker();
      return;
    }
    void generatePortrait();
  };

  const canEnter = Boolean(
    assets?.ready &&
    selfIsReady &&
    partnerIsReady &&
    trimmedName &&
    !pendingFile &&
    !isPortraitGenerating,
  );

  const generateDisabled = Boolean(
    isPortraitGenerating || (pendingFile && !trimmedName),
  );

  if (settingsOpen) {
    return <ProfileSettingsScreen onBack={() => setSettingsOpen(false)} />;
  }

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#eee7da] text-black">
      <CornerControls
        onBack={() => setStep("profile")}
        onOpenSettings={() => setSettingsOpen(true)}
        disabled={isSubmitting}
      />

      <main className="relative z-10 flex h-full w-full flex-col items-center px-[clamp(28px,7vw,58px)] pt-[clamp(56px,6.8vh,88px)] text-center">
        <h1 className="text-[clamp(40px,6.6vw,52px)] font-semibold leading-none tracking-[-0.035em]">
          {t("photo.title")}
        </h1>

        <div className="mt-[clamp(52px,6vh,78px)] flex flex-col items-center">
          <Portrait
            imageUrl={portraitUrl}
            isGenerating={isPortraitGenerating}
            onClick={openPicker}
          />

          <button
            type="button"
            onClick={handleGenerateButton}
            disabled={generateDisabled}
            className={[
              "relative z-10 -mt-[10px] flex min-h-[66px] min-w-[clamp(310px,57vw,420px)] items-center justify-center gap-3 rounded-full bg-black px-8",
              "text-[clamp(24px,4.1vw,31px)] font-semibold text-white shadow-[0_10px_20px_rgba(49,43,36,0.12)] transition",
              "focus:outline-none focus-visible:ring-3 focus-visible:ring-black focus-visible:ring-offset-3 focus-visible:ring-offset-[#eee7da]",
              generateDisabled
                ? "cursor-not-allowed opacity-40"
                : "cursor-pointer hover:-translate-y-0.5 active:translate-y-0",
            ].join(" ")}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className={[
                "h-6 w-6 shrink-0",
                isPortraitGenerating ? "animate-spin" : "",
              ].join(" ")}
            >
              <path d="M20 7h-5V2" />
              <path d="M4 17h5v5" />
              <path d="M5.2 9A8 8 0 0 1 18 4.7L20 7" />
              <path d="M18.8 15A8 8 0 0 1 6 19.3L4 17" />
            </svg>
            <span>
              {isPortraitGenerating
                ? t("photo.generating")
                : t("photo.generatePortait")}
            </span>
          </button>

          <label
            htmlFor="preferred-name"
            className="mt-[30px] text-[clamp(24px,4.1vw,31px)] font-medium text-black/45"
          >
            {t("photo.preferredName")}
          </label>
          <input
            id="preferred-name"
            type="text"
            value={preferredName}
            maxLength={128}
            autoComplete="name"
            disabled={isPortraitGenerating}
            onChange={(event) => updatePreferredName(event.target.value)}
            className="mt-2 h-[70px] w-[clamp(320px,59vw,440px)] rounded-[8px] border border-black/5 bg-white px-5 text-center text-[clamp(25px,4.3vw,33px)] font-medium text-black outline-none shadow-[0_6px_14px_rgba(56,49,41,0.04)] transition focus:border-black/35 focus:ring-2 focus:ring-black/10 disabled:opacity-55"
          />

          {selfIsReady && !pendingFile ? (
            <p className="mt-4 text-[clamp(20px,3.4vw,26px)] font-semibold text-black/55">
              {t("photo.portraitReady")}
            </p>
          ) : null}
        </div>

        <div className="absolute inset-x-[clamp(28px,7vw,58px)] bottom-[clamp(206px,24vh,266px)] text-center">
          {uploadError ? (
            <p className="text-[clamp(18px,3vw,23px)] font-semibold leading-snug text-[#8d382d]">
              {uploadError}
            </p>
          ) : null}
          {statusError ? (
            <p className="mt-2 text-[clamp(18px,3vw,23px)] font-semibold leading-snug text-[#8d382d]">
              {statusError}
            </p>
          ) : null}
        </div>
      </main>

      <NextButton
        disabled={!canEnter}
        isLoading={false}
        onClick={() => setStep("connected")}
      />
      <ProgressIndicator />

      <HiddenPhotoInput
        inputRef={cameraRef}
        capture="user"
        onFile={handleFileSelected}
      />
      <HiddenPhotoInput inputRef={albumRef} onFile={handleFileSelected} />
      <UploadSourcePicker
        visible={pickerOpen}
        onSelect={selectUploadSource}
        onClose={() => setPickerOpen(false)}
      />
    </div>
  );
}
