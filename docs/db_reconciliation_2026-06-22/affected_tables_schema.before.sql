-- DDL REFERENCE SNAPSHOT (pre-reconciliation) 2026-06-22
-- Source: live production DB catalog. Read-only reconstruction.

alembic_version = u5p6q7r8s9t0

==============================================================================
TABLE: bullhorn_environment
==============================================================================
-- columns:
   id                               integer                NOT NULL default nextval('bullhorn_environment_id_seq'::regclass)
   key                              character varying(50)  NOT NULL 
   display_name                     character varying(120) NOT NULL 
   company_name                     character varying(120) NULL     
   is_default                       boolean                NOT NULL 
   is_active                        boolean                NOT NULL 
   bullhorn_client_id               character varying(255) NULL     
   bullhorn_client_secret           character varying(255) NULL     
   bullhorn_username                character varying(255) NULL     
   bullhorn_password                character varying(255) NULL     
   created_at                       timestamp without time zone NULL     
   updated_at                       timestamp without time zone NULL     
   screening_profile                character varying(50)  NULL     
   screening_config_overrides       text                   NULL     
   salesrep_sync_enabled            boolean                NULL     
   salesrep_source_field            character varying(50)  NULL     
   salesrep_display_field           character varying(50)  NULL     
-- constraints:
   bullhorn_environment_pkey: PRIMARY KEY (id)
-- indexes:
   CREATE UNIQUE INDEX bullhorn_environment_pkey ON public.bullhorn_environment USING btree (id)
   CREATE INDEX ix_bullhorn_environment_is_active ON public.bullhorn_environment USING btree (is_active)
   CREATE INDEX ix_bullhorn_environment_is_default ON public.bullhorn_environment USING btree (is_default)
   CREATE UNIQUE INDEX ix_bullhorn_environment_key ON public.bullhorn_environment USING btree (key)
   CREATE UNIQUE INDEX uq_bullhorn_environment_single_default ON public.bullhorn_environment USING btree (is_default) WHERE is_default

==============================================================================
TABLE: environment_brand
==============================================================================
-- columns:
   id                               integer                NOT NULL default nextval('environment_brand_id_seq'::regclass)
   environment_id                   integer                NOT NULL 
   key                              character varying(50)  NOT NULL 
   display_name                     character varying(120) NOT NULL 
   domains                          text                   NULL     
   apply_template                   character varying(120) NOT NULL 
   logo_path                        character varying(255) NULL     
   logo_filename                    character varying(120) NULL     
   logo_cid                         character varying(120) NULL     
   company_name                     character varying(160) NULL     
   logo_alt_text                    character varying(160) NULL     
   from_email                       character varying(255) NULL     
   to_email                         character varying(255) NULL     
   is_default                       boolean                NOT NULL 
   is_active                        boolean                NOT NULL 
   created_at                       timestamp without time zone NULL     
   updated_at                       timestamp without time zone NULL     
-- constraints:
   environment_brand_environment_id_fkey: FOREIGN KEY (environment_id) REFERENCES bullhorn_environment(id)
   environment_brand_pkey: PRIMARY KEY (id)
-- indexes:
   CREATE UNIQUE INDEX environment_brand_pkey ON public.environment_brand USING btree (id)
   CREATE INDEX ix_environment_brand_environment_id ON public.environment_brand USING btree (environment_id)
   CREATE INDEX ix_environment_brand_is_active ON public.environment_brand USING btree (is_active)
   CREATE INDEX ix_environment_brand_is_default ON public.environment_brand USING btree (is_default)
   CREATE UNIQUE INDEX ix_environment_brand_key ON public.environment_brand USING btree (key)
   CREATE UNIQUE INDEX uq_environment_brand_single_default ON public.environment_brand USING btree (is_default) WHERE is_default

==============================================================================
TABLE: user
==============================================================================
-- columns:
   id                               integer                NOT NULL default nextval('user_id_seq'::regclass)
   username                         character varying(80)  NOT NULL 
   email                            character varying(120) NOT NULL 
   password_hash                    character varying(256) NOT NULL 
   is_admin                         boolean                NULL     
   is_company_admin                 boolean                NULL     
   role                             character varying(20)  NULL     
   company                          character varying(100) NULL     
   bullhorn_user_id                 integer                NULL     
   display_name                     character varying(255) NULL     
   subscribed_modules               text                   NULL     
   created_at                       timestamp without time zone NULL     
   last_login                       timestamp without time zone NULL     
   last_active_at                   timestamp without time zone NULL     
   environment_id                   integer                NULL     
-- constraints:
   user_pkey: PRIMARY KEY (id)
   user_email_key: UNIQUE (email)
   user_username_key: UNIQUE (username)
-- indexes:
   CREATE INDEX ix_user_environment_id ON public."user" USING btree (environment_id)
   CREATE UNIQUE INDEX user_email_key ON public."user" USING btree (email)
   CREATE UNIQUE INDEX user_pkey ON public."user" USING btree (id)
   CREATE UNIQUE INDEX user_username_key ON public."user" USING btree (username)

==============================================================================
TABLE: candidate_vetting_log
==============================================================================
-- columns:
   id                               integer                NOT NULL default nextval('candidate_vetting_log_id_seq'::regclass)
   bullhorn_candidate_id            integer                NOT NULL 
   candidate_name                   character varying(255) NULL     
   candidate_email                  character varying(255) NULL     
   applied_job_id                   integer                NULL     
   applied_job_title                character varying(500) NULL     
   parsed_email_id                  integer                NULL     
   resume_text                      text                   NULL     
   resume_file_id                   integer                NULL     
   status                           character varying(50)  NULL     
   is_qualified                     boolean                NULL     
   highest_match_score              double precision       NULL     
   total_jobs_matched               integer                NULL     
   note_created                     boolean                NULL     
   bullhorn_note_id                 integer                NULL     
   notifications_sent               boolean                NULL     
   notification_count               integer                NULL     
   error_message                    text                   NULL     
   retry_count                      integer                NULL     
   retry_blocked                    boolean                NULL     default false
   retry_block_reason               character varying(500) NULL     
   is_sandbox                       boolean                NULL     default false
   detected_at                      timestamp without time zone NULL     
   analyzed_at                      timestamp without time zone NULL     
   created_at                       timestamp without time zone NULL     
   updated_at                       timestamp without time zone NULL     
   candidate_phone                  character varying(32)  NULL     
   candidate_linkedin_url           character varying(255) NULL     
   environment_id                   integer                NULL     
-- constraints:
   candidate_vetting_log_pkey: PRIMARY KEY (id)
-- indexes:
   CREATE UNIQUE INDEX candidate_vetting_log_pkey ON public.candidate_vetting_log USING btree (id)
   CREATE INDEX idx_vetting_log_status_created ON public.candidate_vetting_log USING btree (status, created_at)
   CREATE INDEX ix_candidate_vetting_log_bullhorn_candidate_id ON public.candidate_vetting_log USING btree (bullhorn_candidate_id)
   CREATE INDEX ix_candidate_vetting_log_candidate_email ON public.candidate_vetting_log USING btree (candidate_email)
   CREATE INDEX ix_candidate_vetting_log_candidate_linkedin_url ON public.candidate_vetting_log USING btree (candidate_linkedin_url)
   CREATE INDEX ix_candidate_vetting_log_candidate_phone ON public.candidate_vetting_log USING btree (candidate_phone)
   CREATE INDEX ix_candidate_vetting_log_environment_id ON public.candidate_vetting_log USING btree (environment_id)
   CREATE INDEX ix_candidate_vetting_log_parsed_email_id ON public.candidate_vetting_log USING btree (parsed_email_id)

==============================================================================
TABLE: candidate_job_match
==============================================================================
-- columns:
   id                               integer                NOT NULL default nextval('candidate_job_match_id_seq'::regclass)
   vetting_log_id                   integer                NOT NULL 
   bullhorn_job_id                  integer                NOT NULL 
   job_title                        character varying(500) NULL     
   job_location                     character varying(255) NULL     
   tearsheet_id                     integer                NULL     
   tearsheet_name                   character varying(255) NULL     
   recruiter_name                   character varying(255) NULL     
   recruiter_email                  character varying(255) NULL     
   recruiter_bullhorn_id            integer                NULL     
   match_score                      double precision       NOT NULL 
   technical_score                  double precision       NULL     
   is_qualified                     boolean                NULL     
   is_applied_job                   boolean                NULL     
   match_summary                    text                   NULL     
   skills_match                     text                   NULL     
   experience_match                 text                   NULL     
   gaps_identified                  text                   NULL     
   years_analysis_json              text                   NULL     
   prestige_employer                character varying(255) NULL     
   prestige_boost_applied           boolean                NULL     
   notification_sent                boolean                NULL     
   notification_sent_at             timestamp without time zone NULL     
   created_at                       timestamp without time zone NULL     
   environment_id                   integer                NULL     
-- constraints:
   candidate_job_match_vetting_log_id_fkey: FOREIGN KEY (vetting_log_id) REFERENCES candidate_vetting_log(id)
   candidate_job_match_pkey: PRIMARY KEY (id)
-- indexes:
   CREATE UNIQUE INDEX candidate_job_match_pkey ON public.candidate_job_match USING btree (id)
   CREATE INDEX idx_match_job_created ON public.candidate_job_match USING btree (bullhorn_job_id, created_at)
   CREATE INDEX ix_candidate_job_match_bullhorn_job_id ON public.candidate_job_match USING btree (bullhorn_job_id)
   CREATE INDEX ix_candidate_job_match_environment_id ON public.candidate_job_match USING btree (environment_id)
   CREATE INDEX ix_candidate_job_match_vetting_log_id ON public.candidate_job_match USING btree (vetting_log_id)

==============================================================================
TABLE: job_vetting_requirements
==============================================================================
-- columns:
   id                               integer                NOT NULL default nextval('job_vetting_requirements_id_seq'::regclass)
   bullhorn_job_id                  integer                NOT NULL 
   job_title                        character varying(255) NULL     
   job_location                     character varying(255) NULL     
   job_work_type                    character varying(50)  NULL     
   custom_requirements              text                   NULL     
   ai_interpreted_requirements      text                   NULL     
   edited_requirements              text                   NULL     
   requirements_edited_at           timestamp without time zone NULL     
   requirements_edited_by           character varying(255) NULL     
   vetting_threshold                integer                NULL     
   scout_vetting_enabled            boolean                NULL     
   employer_prestige_boost          boolean                NULL     
   last_ai_interpretation           timestamp without time zone NULL     
   created_at                       timestamp without time zone NULL     
   updated_at                       timestamp without time zone NULL     
   environment_id                   integer                NULL     
-- constraints:
   job_vetting_requirements_pkey: PRIMARY KEY (id)
-- indexes:
   CREATE UNIQUE INDEX ix_job_vetting_requirements_bullhorn_job_id ON public.job_vetting_requirements USING btree (bullhorn_job_id)
   CREATE INDEX ix_job_vetting_requirements_environment_id ON public.job_vetting_requirements USING btree (environment_id)
   CREATE UNIQUE INDEX job_vetting_requirements_pkey ON public.job_vetting_requirements USING btree (id)
   CREATE UNIQUE INDEX uq_jvr_env_job ON public.job_vetting_requirements USING btree (environment_id, bullhorn_job_id)

==============================================================================
TABLE: parsed_email
==============================================================================
-- columns:
   id                               integer                NOT NULL default nextval('parsed_email_id_seq'::regclass)
   message_id                       character varying(255) NULL     
   sender_email                     character varying(255) NOT NULL 
   recipient_email                  character varying(255) NOT NULL 
   subject                          character varying(500) NULL     
   source_platform                  character varying(50)  NULL     
   bullhorn_job_id                  integer                NULL     
   candidate_name                   character varying(255) NULL     
   candidate_email                  character varying(255) NULL     
   candidate_phone                  character varying(50)  NULL     
   status                           character varying(50)  NULL     
   processing_notes                 text                   NULL     
   bullhorn_candidate_id            integer                NULL     
   bullhorn_submission_id           integer                NULL     
   is_duplicate_candidate           boolean                NULL     
   duplicate_confidence             double precision       NULL     
   resume_filename                  character varying(255) NULL     
   resume_file_id                   integer                NULL     
   received_at                      timestamp without time zone NULL     
   processed_at                     timestamp without time zone NULL     
   created_at                       timestamp without time zone NULL     
   vetted_at                        timestamp without time zone NULL     
   vetting_retry_count              integer                NULL     default 0
   environment_id                   integer                NULL     
   recovery_message_id              character varying(255) NULL     
-- constraints:
   parsed_email_pkey: PRIMARY KEY (id)
   parsed_email_message_id_key: UNIQUE (message_id)
-- indexes:
   CREATE INDEX idx_parsed_email_unvetted ON public.parsed_email USING btree (status, vetted_at, bullhorn_candidate_id)
   CREATE INDEX ix_parsed_email_candidate_email ON public.parsed_email USING btree (candidate_email)
   CREATE INDEX ix_parsed_email_environment_id ON public.parsed_email USING btree (environment_id)
   CREATE UNIQUE INDEX parsed_email_message_id_key ON public.parsed_email USING btree (message_id)
   CREATE UNIQUE INDEX parsed_email_pkey ON public.parsed_email USING btree (id)

==============================================================================
TABLE: bullhorn_monitor
==============================================================================
-- columns:
   id                               integer                NOT NULL default nextval('bullhorn_monitor_id_seq'::regclass)
   name                             character varying(100) NOT NULL 
   tearsheet_id                     integer                NOT NULL 
   tearsheet_name                   character varying(255) NULL     
   is_active                        boolean                NULL     
   check_interval_minutes           integer                NULL     
   last_check                       timestamp without time zone NULL     
   next_check                       timestamp without time zone NOT NULL 
   notification_email               character varying(255) NULL     
   send_notifications               boolean                NULL     
   last_job_snapshot                text                   NULL     
   created_at                       timestamp without time zone NULL     
   updated_at                       timestamp without time zone NULL     
   environment_id                   integer                NULL     
-- constraints:
   bullhorn_monitor_pkey: PRIMARY KEY (id)
-- indexes:
   CREATE UNIQUE INDEX bullhorn_monitor_pkey ON public.bullhorn_monitor USING btree (id)
   CREATE INDEX ix_bullhorn_monitor_environment_id ON public.bullhorn_monitor USING btree (environment_id)

==============================================================================
TABLE: candidate_fraud_assessment
==============================================================================
-- columns:
   id                               bigint                 NOT NULL default nextval('candidate_fraud_assessment_id_seq'::regclass)
   created_at                       timestamp without time zone NOT NULL 
   bullhorn_candidate_id            integer                NULL     
   vetting_log_id                   integer                NULL     
   candidate_name                   character varying(200) NULL     
   candidate_email                  character varying(255) NULL     
   risk_score                       integer                NOT NULL 
   risk_band                        character varying(20)  NOT NULL 
   signals_json                     text                   NULL     
   trigger                          character varying(20)  NOT NULL 
   note_created                     boolean                NOT NULL 
   bullhorn_note_id                 integer                NULL     
   evaluation_error                 text                   NULL     
   environment_id                   integer                NULL     
-- constraints:
   candidate_fraud_assessment_pkey: PRIMARY KEY (id)
-- indexes:
   CREATE UNIQUE INDEX candidate_fraud_assessment_pkey ON public.candidate_fraud_assessment USING btree (id)
   CREATE INDEX ix_candidate_fraud_assessment_band_created ON public.candidate_fraud_assessment USING btree (risk_band, created_at)
   CREATE INDEX ix_candidate_fraud_assessment_bullhorn_candidate_id ON public.candidate_fraud_assessment USING btree (bullhorn_candidate_id)
   CREATE INDEX ix_candidate_fraud_assessment_cand_created ON public.candidate_fraud_assessment USING btree (bullhorn_candidate_id, created_at)
   CREATE INDEX ix_candidate_fraud_assessment_created_at ON public.candidate_fraud_assessment USING btree (created_at)
   CREATE INDEX ix_candidate_fraud_assessment_environment_id ON public.candidate_fraud_assessment USING btree (environment_id)
   CREATE INDEX ix_candidate_fraud_assessment_risk_band ON public.candidate_fraud_assessment USING btree (risk_band)
   CREATE INDEX ix_candidate_fraud_assessment_vetting_log_id ON public.candidate_fraud_assessment USING btree (vetting_log_id)

==============================================================================
TABLE: job_embedding
==============================================================================
-- columns:
   id                               integer                NOT NULL default nextval('job_embedding_id_seq'::regclass)
   bullhorn_job_id                  integer                NOT NULL 
   job_title                        character varying(500) NULL     
   description_hash                 character varying(64)  NOT NULL 
   embedding_vector                 text                   NOT NULL 
   embedding_model                  character varying(50)  NOT NULL 
   created_at                       timestamp without time zone NULL     
   updated_at                       timestamp without time zone NULL     
   environment_id                   integer                NULL     
-- constraints:
   job_embedding_pkey: PRIMARY KEY (id)
-- indexes:
   CREATE UNIQUE INDEX ix_job_embedding_bullhorn_job_id ON public.job_embedding USING btree (bullhorn_job_id)
   CREATE INDEX ix_job_embedding_environment_id ON public.job_embedding USING btree (environment_id)
   CREATE UNIQUE INDEX job_embedding_pkey ON public.job_embedding USING btree (id)
   CREATE UNIQUE INDEX uq_je_env_job ON public.job_embedding USING btree (environment_id, bullhorn_job_id)

==============================================================================
TABLE: candidate_profile_embedding
==============================================================================
-- columns:
   id                               integer                NOT NULL default nextval('candidate_profile_embedding_id_seq'::regclass)
   bullhorn_candidate_id            integer                NOT NULL 
   candidate_name                   character varying(200) NULL     
   profile_hash                     character varying(64)  NOT NULL 
   embedding_vector                 text                   NOT NULL 
   embedding_model                  character varying(50)  NOT NULL 
   profile_text_snippet             text                   NULL     
   created_at                       timestamp without time zone NULL     
   updated_at                       timestamp without time zone NULL     
   environment_id                   integer                NULL     
-- constraints:
   candidate_profile_embedding_pkey: PRIMARY KEY (id)
-- indexes:
   CREATE UNIQUE INDEX candidate_profile_embedding_pkey ON public.candidate_profile_embedding USING btree (id)
   CREATE INDEX idx_cand_profile_emb_updated ON public.candidate_profile_embedding USING btree (updated_at)
   CREATE UNIQUE INDEX ix_candidate_profile_embedding_bullhorn_candidate_id ON public.candidate_profile_embedding USING btree (bullhorn_candidate_id)
   CREATE INDEX ix_candidate_profile_embedding_environment_id ON public.candidate_profile_embedding USING btree (environment_id)
   CREATE UNIQUE INDEX uq_cpe_env_candidate ON public.candidate_profile_embedding USING btree (environment_id, bullhorn_candidate_id)

==============================================================================
TABLE: recruiter_notification_ledger
==============================================================================
-- columns:
   id                               integer                NOT NULL default nextval('recruiter_notification_ledger_id_seq'::regclass)
   bullhorn_candidate_id            integer                NOT NULL 
   bullhorn_job_id                  integer                NOT NULL 
   notification_type                character varying(64)  NOT NULL default 'qualified'::character varying
   sent_at                          timestamp without time zone NOT NULL 
   environment_id                   integer                NULL     
-- constraints:
   recruiter_notification_ledger_pkey: PRIMARY KEY (id)
   uq_recruiter_notification_ledger: UNIQUE (bullhorn_candidate_id, bullhorn_job_id, notification_type)
-- indexes:
   CREATE INDEX ix_recruiter_notification_ledger_bullhorn_candidate_id ON public.recruiter_notification_ledger USING btree (bullhorn_candidate_id)
   CREATE INDEX ix_recruiter_notification_ledger_bullhorn_job_id ON public.recruiter_notification_ledger USING btree (bullhorn_job_id)
   CREATE INDEX ix_recruiter_notification_ledger_environment_id ON public.recruiter_notification_ledger USING btree (environment_id)
   CREATE INDEX ix_recruiter_notification_ledger_notification_type ON public.recruiter_notification_ledger USING btree (notification_type)
   CREATE INDEX ix_recruiter_notification_ledger_sent_at ON public.recruiter_notification_ledger USING btree (sent_at)
   CREATE UNIQUE INDEX recruiter_notification_ledger_pkey ON public.recruiter_notification_ledger USING btree (id)
   CREATE UNIQUE INDEX uq_recruiter_notification_ledger ON public.recruiter_notification_ledger USING btree (bullhorn_candidate_id, bullhorn_job_id, notification_type)

