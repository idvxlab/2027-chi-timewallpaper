-- CHI TimeWallpaper MySQL database schema
-- MySQL 8.0+
--
-- Design boundary:
-- 1. RawMessage stores original user messages only.
-- 2. InteractionRecord stores raw interaction behavior only.
-- 3. SemanticEvidence stores structured evidence extracted from raw messages/interactions
--    according to the A-F semantic variable tables.
-- 4. History Reasoner reads SemanticEvidence + InteractionRecord and outputs the current
--    long-term table to Designer in memory. Designer does not read database tables.

CREATE DATABASE IF NOT EXISTS chi_timewallpaper
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE chi_timewallpaper;

CREATE TABLE users (
  user_id VARCHAR(64) PRIMARY KEY,
  display_name VARCHAR(128) NOT NULL DEFAULT '',
  role ENUM('father', 'mother', 'son', 'daughter', 'admin', 'unknown') NOT NULL DEFAULT 'unknown',
  profile_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB;

CREATE TABLE relationships (
  relationship_id VARCHAR(64) PRIMARY KEY,
  parent_user_id VARCHAR(64) NOT NULL,
  child_user_id VARCHAR(64) NOT NULL,
  parent_role ENUM('father', 'mother') NOT NULL,
  child_role ENUM('son', 'daughter') NOT NULL,
  relation_type VARCHAR(64) NOT NULL DEFAULT 'parent_child',
  profile_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_relationship_parent
    FOREIGN KEY (parent_user_id) REFERENCES users(user_id),
  CONSTRAINT fk_relationship_child
    FOREIGN KEY (child_user_id) REFERENCES users(user_id),
  CONSTRAINT chk_relationship_two_distinct_users
    CHECK (parent_user_id <> child_user_id),
  UNIQUE KEY uk_parent_child_pair (parent_user_id, child_user_id),
  INDEX idx_relationship_parent (parent_user_id),
  INDEX idx_relationship_child (child_user_id)
) ENGINE=InnoDB;

CREATE TABLE raw_messages (
  message_id VARCHAR(64) PRIMARY KEY,
  relationship_id VARCHAR(64) NOT NULL,
  sender_user_id VARCHAR(64) NOT NULL,
  input_type ENUM('text', 'audio') NOT NULL,
  transcript TEXT NOT NULL,
  audio_url VARCHAR(512) NULL,
  audio_duration_ms INT NULL,
  client_created_at DATETIME(3) NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_raw_message_relationship
    FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id),
  CONSTRAINT fk_raw_message_sender
    FOREIGN KEY (sender_user_id) REFERENCES users(user_id),
  INDEX idx_raw_message_relationship_time (relationship_id, created_at),
  INDEX idx_raw_message_sender_time (sender_user_id, created_at)
) ENGINE=InnoDB;

CREATE TABLE interaction_records (
  interaction_id VARCHAR(64) PRIMARY KEY,
  relationship_id VARCHAR(64) NOT NULL,
  actor_user_id VARCHAR(64) NOT NULL,
  interaction_type ENUM('press', 'hold', 'slide', 'flip') NOT NULL,
  target_type VARCHAR(64) NOT NULL DEFAULT '',
  target_id VARCHAR(128) NOT NULL DEFAULT '',
  direction ENUM('left', 'right', 'up', 'down', 'none') NOT NULL DEFAULT 'none',
  duration_ms INT NULL,
  payload_json JSON NULL,
  client_created_at DATETIME(3) NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_interaction_relationship
    FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id),
  CONSTRAINT fk_interaction_actor
    FOREIGN KEY (actor_user_id) REFERENCES users(user_id),
  INDEX idx_interaction_relationship_time (relationship_id, created_at),
  INDEX idx_interaction_type_time (relationship_id, interaction_type, created_at),
  INDEX idx_interaction_actor_time (actor_user_id, created_at)
) ENGINE=InnoDB;

CREATE TABLE agent_runs (
  run_id VARCHAR(64) PRIMARY KEY,
  relationship_id VARCHAR(64) NOT NULL,
  trigger_message_id VARCHAR(64) NULL,
  run_type ENUM('text', 'audio', 'interaction', 'manual_rebuild') NOT NULL,
  status ENUM('running', 'done', 'failed') NOT NULL DEFAULT 'running',
  error_message TEXT NULL,
  payload_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_agent_run_relationship
    FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id),
  CONSTRAINT fk_agent_run_message
    FOREIGN KEY (trigger_message_id) REFERENCES raw_messages(message_id),
  INDEX idx_agent_run_relationship_time (relationship_id, created_at),
  INDEX idx_agent_run_message (trigger_message_id)
) ENGINE=InnoDB;

CREATE TABLE semantic_variables (
  variable_id SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  category_code CHAR(1) NOT NULL,
  category_name VARCHAR(128) NOT NULL,
  variable_key VARCHAR(64) NOT NULL,
  variable_name VARCHAR(128) NOT NULL,
  time_scope ENUM('short_term', 'long_term', 'interaction') NOT NULL,
  input_modality ENUM('language', 'touch', 'system') NOT NULL,
  communication_question VARCHAR(255) NOT NULL DEFAULT '',
  candidate_values_json JSON NULL,
  mapping_layers_json JSON NULL,
  visual_mapping VARCHAR(255) NOT NULL DEFAULT '',
  UNIQUE KEY uk_semantic_variable_key (variable_key),
  INDEX idx_semantic_variable_category (category_code, time_scope)
) ENGINE=InnoDB;

CREATE TABLE semantic_evidence (
  evidence_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  relationship_id VARCHAR(64) NOT NULL,
  variable_id SMALLINT UNSIGNED NOT NULL,
  source_record_type ENUM('raw_message', 'interaction', 'agent_run') NOT NULL,
  message_id VARCHAR(64) NULL,
  interaction_id VARCHAR(64) NULL,
  run_id VARCHAR(64) NULL,
  source_agent ENUM('script_analyzer', 'history_reasoner', 'interaction_logger', 'system') NOT NULL,
  value_text VARCHAR(255) NOT NULL,
  value_json JSON NULL,
  evidence_text TEXT NULL,
  confidence DECIMAL(4,3) NOT NULL DEFAULT 0.500,
  observed_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_semantic_evidence_relationship
    FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id),
  CONSTRAINT fk_semantic_evidence_variable
    FOREIGN KEY (variable_id) REFERENCES semantic_variables(variable_id),
  CONSTRAINT fk_semantic_evidence_message
    FOREIGN KEY (message_id) REFERENCES raw_messages(message_id),
  CONSTRAINT fk_semantic_evidence_interaction
    FOREIGN KEY (interaction_id) REFERENCES interaction_records(interaction_id),
  CONSTRAINT fk_semantic_evidence_run
    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id),
  INDEX idx_evidence_relationship_time (relationship_id, observed_at),
  INDEX idx_evidence_variable_time (relationship_id, variable_id, observed_at),
  INDEX idx_evidence_message (message_id),
  INDEX idx_evidence_interaction (interaction_id),
  INDEX idx_evidence_run (run_id)
) ENGINE=InnoDB;

CREATE TABLE reference_images (
  reference_id VARCHAR(64) PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL,
  relationship_id VARCHAR(64) NULL,
  role ENUM('elder', 'child') NOT NULL,
  image_url VARCHAR(512) NOT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_reference_user
    FOREIGN KEY (user_id) REFERENCES users(user_id),
  CONSTRAINT fk_reference_relationship
    FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id),
  INDEX idx_reference_user_role (user_id, role, created_at),
  INDEX idx_reference_relationship_role (relationship_id, role, created_at)
) ENGINE=InnoDB;

CREATE TABLE wallpaper_records (
  wallpaper_id VARCHAR(64) PRIMARY KEY,
  relationship_id VARCHAR(64) NOT NULL,
  message_id VARCHAR(64) NULL,
  run_id VARCHAR(64) NOT NULL,
  image_url VARCHAR(512) NOT NULL DEFAULT '',
  final_prompt TEXT NULL,
  asset_metadata_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_wallpaper_relationship
    FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id),
  CONSTRAINT fk_wallpaper_message
    FOREIGN KEY (message_id) REFERENCES raw_messages(message_id),
  CONSTRAINT fk_wallpaper_run
    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id),
  INDEX idx_wallpaper_relationship_time (relationship_id, created_at),
  INDEX idx_wallpaper_message (message_id),
  INDEX idx_wallpaper_run (run_id)
) ENGINE=InnoDB;

INSERT INTO semantic_variables
  (category_code, category_name, variable_key, variable_name, time_scope, input_modality, communication_question, candidate_values_json, mapping_layers_json, visual_mapping)
VALUES
  ('A', 'Situational Semantics 情境事实语义', 'event', 'Event 生活事件', 'short_term', 'language', '最近发生了什么？',
   JSON_ARRAY('工作', '做饭', '散步', '旅行', '生病', '聚会', '学习', '通勤'),
   JSON_ARRAY('L3'), '日常活动场景、行为动作、事件物件、生活轨迹'),
  ('A', 'Situational Semantics 情境事实语义', 'scene', 'Scene 生活场景', 'short_term', 'language', '发生在哪里？对方在哪里？',
   JSON_ARRAY('家庭', '社区', '公园', '办公室', '医院', '街道', '阳台', '厨房'),
   JSON_ARRAY('L1'), '房屋、街道、公园、厨房、办公室、医院等环境结构'),
  ('A', 'Situational Semantics 情境事实语义', 'time', 'Time 时间状态', 'short_term', 'language', '何时发生？对方当前生活节奏如何？',
   JSON_ARRAY('清晨', '午后', '黄昏', '深夜', '周末', '节日', '季节'),
   JSON_ARRAY('L1'), '日出、夕阳、夜灯、月光、星空、季节光影'),
  ('A', 'Situational Semantics 情境事实语义', 'subject', 'Subject 人物对象', 'short_term', 'language', '与谁有关？谁在场？',
   JSON_ARRAY('父母', '子女', '宠物', '朋友', '邻居', '同事'),
   JSON_ARRAY('L4'), '人物角色、宠物角色、群体关系、人物相对位置'),
  ('A', 'Situational Semantics 情境事实语义', 'object', 'Object 物件', 'short_term', 'language', '哪些物件提示了这件事？',
   JSON_ARRAY('饭菜', '药盒', '行李箱', '书', '照片', '花', '杯子', '门票'),
   JSON_ARRAY('L1', 'L3'), '关键物件作为事件锚点，放在生活场景中形成可识别线索'),

  ('B', 'Affective Semantics 情绪状态语义', 'momentary_affect', 'Momentary Affect 当下情绪类型', 'short_term', 'language', '此刻感受如何？',
   JSON_ARRAY('愉悦', '平静', '悲伤', '焦虑', '思念', '期待'),
   JSON_ARRAY('L1', 'L4'), '画面色温和角色表情/姿态/动作状态共同表达情绪'),
  ('B', 'Affective Semantics 情绪状态语义', 'affective_intensity', 'Affective Intensity 情绪强度', 'short_term', 'language', '这种感受有多强？',
   JSON_ARRAY('轻微', '明显', '强烈', '波动'),
   JSON_ARRAY('L1'), '画面饱和度变化'),
  ('B', 'Affective Semantics 情绪状态语义', 'affective_ambiguity', 'Affective Ambiguity 情绪模糊度', 'short_term', 'language', '这个状态是否容易理解？',
   JSON_ARRAY('明确', '含混', '难以判断', '需要上下文'),
   JSON_ARRAY('L1', 'L4'), '人脸、姿态、动作、表情清晰度'),

  ('C', 'Communicative Semantics 沟通意图语义', 'intent_type', 'Intent Type 沟通意图类型', 'short_term', 'language', '为什么留下这条信息？',
   JSON_ARRAY('分享生活', '表达思念', '寻求安慰', '倾诉情绪', '期待回应'),
   JSON_ARRAY('L2', 'L5'), '通过叙事焦点、连接线索、回应提示来突出分享动机'),
  ('C', 'Communicative Semantics 沟通意图语义', 'desired_response', 'Desired Response 期待回应方式', 'short_term', 'language', '希望对方怎样回应？',
   JSON_ARRAY('看见即可', '轻触回应', '留言', '回忆', '进一步聊天'),
   JSON_ARRAY('L2', 'L5'), '灯光闪烁、光点等低压力提示'),

  ('D', 'Longitudinal State Semantics 长期心理状态语义', 'social_connection_cue', 'Social Connection Cue 社会连接线索', 'long_term', 'language', '双方联系如何？',
   JSON_ARRAY('陪伴减少', '联系需求增加', '独处时间变长', '群体活动变少'),
   JSON_ARRAY('L1', 'L2', 'L5'), '空间空旷化'),
  ('D', 'Longitudinal State Semantics 长期心理状态语义', 'fatigue_vitality_cue', 'Fatigue / Vitality Cue 疲惫/活力线索', 'long_term', 'language', '双方距离如何？',
   JSON_ARRAY('活动减少', '动作变慢', '休息增多', '活力恢复'),
   JSON_ARRAY('L4'), '角色动作速度'),
  ('D', 'Longitudinal State Semantics 长期心理状态语义', 'stability_fluctuation', 'Stability / Fluctuation 稳定性/波动', 'long_term', 'language', '关系是否有来有回？',
   JSON_ARRAY('状态稳定', '情绪波动', '生活节奏紊乱', '恢复稳定'),
   JSON_ARRAY('L1'), '角色表情状态'),

  ('E', 'Relational Semantics 关系语义', 'interaction_frequency', 'Interaction Frequency 互动频率', 'long_term', 'language', '双方联系如何？',
   JSON_ARRAY('经常联系', '偶尔联系', '长时间未联系', '突然密集'),
   JSON_ARRAY('L2', 'L5'), '房间里的生活痕迹、更新内容、可见生活碎片'),
  ('E', 'Relational Semantics 关系语义', 'intimacy_distance', 'Intimacy / Distance 亲密度/距离感', 'long_term', 'language', '双方距离如何？',
   JSON_ARRAY('近', '远', '共享空间大小', '边界感强弱'),
   JSON_ARRAY('L1', 'L2'), '两个空间之间的距离、共享区域、路径、边界'),
  ('E', 'Relational Semantics 关系语义', 'emotional_warmth', 'Emotional Warmth 情感温度', 'long_term', 'language', '关系是否温暖？',
   JSON_ARRAY('温暖', '疏离', '稳定', '冷淡'),
   JSON_ARRAY('L1', 'L2'), '花苞/果实越多'),
  ('E', 'Relational Semantics 关系语义', 'relationship_trend', 'Relationship Trend 关系变化趋势', 'long_term', 'language', '关系正在如何变化？',
   JSON_ARRAY('靠近', '疏远', '修复', '停滞'),
   JSON_ARRAY('L2', 'L5'), '谁说的多，谁侧的空间变得更大'),

  ('F', 'Interaction Semantics 交互语义', 'press', 'Press 轻触', 'interaction', 'touch', '如何查看对方状态？',
   JSON_ARRAY('轻触人物', '物件查看对方状态'),
   JSON_ARRAY('L4', 'L5'), '局部发光、触摸区域反馈'),
  ('F', 'Interaction Semantics 交互语义', 'hold', 'Hold 长按', 'interaction', 'touch', '如何低门槛回应？',
   JSON_ARRAY('语音输入'),
   JSON_ARRAY('L4', 'L5'), '涟漪'),
  ('F', 'Interaction Semantics 交互语义', 'slide', 'Slide 左右或上下滑动', 'interaction', 'touch', '如何理解时间变化？',
   JSON_ARRAY('左右查看过去', '左右浏览历史记录', '上下查看记忆资产'),
   JSON_ARRAY('card'), '前后日的历史记录、聊天内容精华可视化总结'),
  ('F', 'Interaction Semantics 交互语义', 'flip', 'Flip 点击翻转', 'interaction', 'touch', '如何展开微叙事？',
   JSON_ARRAY('点击查看卡片背后的文字小结', '互动记录', '来源线索'),
   JSON_ARRAY('card'), '正面为图像，背面为简短文字/记录/语义来源');
