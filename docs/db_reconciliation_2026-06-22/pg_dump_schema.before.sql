--
-- PostgreSQL database dump
--

\restrict IVylmMzPiXycrbRUGcr2SBrkxNZCzYtPVSw2zvpfgC0eWL7sFnGkClca3FeF2qD

-- Dumped from database version 16.10
-- Dumped by pg_dump version 16.10

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: bullhorn_environment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bullhorn_environment (
    id integer NOT NULL,
    key character varying(50) NOT NULL,
    display_name character varying(120) NOT NULL,
    company_name character varying(120),
    is_default boolean NOT NULL,
    is_active boolean NOT NULL,
    bullhorn_client_id character varying(255),
    bullhorn_client_secret character varying(255),
    bullhorn_username character varying(255),
    bullhorn_password character varying(255),
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    screening_profile character varying(50),
    screening_config_overrides text,
    salesrep_sync_enabled boolean,
    salesrep_source_field character varying(50),
    salesrep_display_field character varying(50)
);


--
-- Name: bullhorn_environment_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bullhorn_environment_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bullhorn_environment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bullhorn_environment_id_seq OWNED BY public.bullhorn_environment.id;


--
-- Name: bullhorn_monitor; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bullhorn_monitor (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    tearsheet_id integer NOT NULL,
    tearsheet_name character varying(255),
    is_active boolean,
    check_interval_minutes integer,
    last_check timestamp without time zone,
    next_check timestamp without time zone NOT NULL,
    notification_email character varying(255),
    send_notifications boolean,
    last_job_snapshot text,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    environment_id integer
);


--
-- Name: bullhorn_monitor_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bullhorn_monitor_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bullhorn_monitor_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bullhorn_monitor_id_seq OWNED BY public.bullhorn_monitor.id;


--
-- Name: candidate_fraud_assessment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candidate_fraud_assessment (
    id bigint NOT NULL,
    created_at timestamp without time zone NOT NULL,
    bullhorn_candidate_id integer,
    vetting_log_id integer,
    candidate_name character varying(200),
    candidate_email character varying(255),
    risk_score integer NOT NULL,
    risk_band character varying(20) NOT NULL,
    signals_json text,
    trigger character varying(20) NOT NULL,
    note_created boolean NOT NULL,
    bullhorn_note_id integer,
    evaluation_error text,
    environment_id integer
);


--
-- Name: candidate_fraud_assessment_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.candidate_fraud_assessment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: candidate_fraud_assessment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.candidate_fraud_assessment_id_seq OWNED BY public.candidate_fraud_assessment.id;


--
-- Name: candidate_job_match; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candidate_job_match (
    id integer NOT NULL,
    vetting_log_id integer NOT NULL,
    bullhorn_job_id integer NOT NULL,
    job_title character varying(500),
    job_location character varying(255),
    tearsheet_id integer,
    tearsheet_name character varying(255),
    recruiter_name character varying(255),
    recruiter_email character varying(255),
    recruiter_bullhorn_id integer,
    match_score double precision NOT NULL,
    technical_score double precision,
    is_qualified boolean,
    is_applied_job boolean,
    match_summary text,
    skills_match text,
    experience_match text,
    gaps_identified text,
    years_analysis_json text,
    prestige_employer character varying(255),
    prestige_boost_applied boolean,
    notification_sent boolean,
    notification_sent_at timestamp without time zone,
    created_at timestamp without time zone,
    environment_id integer
);


--
-- Name: candidate_job_match_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.candidate_job_match_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: candidate_job_match_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.candidate_job_match_id_seq OWNED BY public.candidate_job_match.id;


--
-- Name: candidate_profile_embedding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candidate_profile_embedding (
    id integer NOT NULL,
    bullhorn_candidate_id integer NOT NULL,
    candidate_name character varying(200),
    profile_hash character varying(64) NOT NULL,
    embedding_vector text NOT NULL,
    embedding_model character varying(50) NOT NULL,
    profile_text_snippet text,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    environment_id integer
)
WITH (autovacuum_vacuum_scale_factor='0.05', autovacuum_vacuum_threshold='50', autovacuum_analyze_scale_factor='0.05', autovacuum_analyze_threshold='50', toast.autovacuum_vacuum_scale_factor='0.05', toast.autovacuum_vacuum_threshold='50');


--
-- Name: candidate_profile_embedding_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.candidate_profile_embedding_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: candidate_profile_embedding_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.candidate_profile_embedding_id_seq OWNED BY public.candidate_profile_embedding.id;


--
-- Name: candidate_vetting_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candidate_vetting_log (
    id integer NOT NULL,
    bullhorn_candidate_id integer NOT NULL,
    candidate_name character varying(255),
    candidate_email character varying(255),
    applied_job_id integer,
    applied_job_title character varying(500),
    parsed_email_id integer,
    resume_text text,
    resume_file_id integer,
    status character varying(50),
    is_qualified boolean,
    highest_match_score double precision,
    total_jobs_matched integer,
    note_created boolean,
    bullhorn_note_id integer,
    notifications_sent boolean,
    notification_count integer,
    error_message text,
    retry_count integer,
    retry_blocked boolean DEFAULT false,
    retry_block_reason character varying(500),
    is_sandbox boolean DEFAULT false,
    detected_at timestamp without time zone,
    analyzed_at timestamp without time zone,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    candidate_phone character varying(32),
    candidate_linkedin_url character varying(255),
    environment_id integer
);


--
-- Name: candidate_vetting_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.candidate_vetting_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: candidate_vetting_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.candidate_vetting_log_id_seq OWNED BY public.candidate_vetting_log.id;


--
-- Name: environment_brand; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.environment_brand (
    id integer NOT NULL,
    environment_id integer NOT NULL,
    key character varying(50) NOT NULL,
    display_name character varying(120) NOT NULL,
    domains text,
    apply_template character varying(120) NOT NULL,
    logo_path character varying(255),
    logo_filename character varying(120),
    logo_cid character varying(120),
    company_name character varying(160),
    logo_alt_text character varying(160),
    from_email character varying(255),
    to_email character varying(255),
    is_default boolean NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: environment_brand_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.environment_brand_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: environment_brand_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.environment_brand_id_seq OWNED BY public.environment_brand.id;


--
-- Name: job_embedding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.job_embedding (
    id integer NOT NULL,
    bullhorn_job_id integer NOT NULL,
    job_title character varying(500),
    description_hash character varying(64) NOT NULL,
    embedding_vector text NOT NULL,
    embedding_model character varying(50) NOT NULL,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    environment_id integer
);


--
-- Name: job_embedding_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.job_embedding_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: job_embedding_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.job_embedding_id_seq OWNED BY public.job_embedding.id;


--
-- Name: job_vetting_requirements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.job_vetting_requirements (
    id integer NOT NULL,
    bullhorn_job_id integer NOT NULL,
    job_title character varying(255),
    job_location character varying(255),
    job_work_type character varying(50),
    custom_requirements text,
    ai_interpreted_requirements text,
    edited_requirements text,
    requirements_edited_at timestamp without time zone,
    requirements_edited_by character varying(255),
    vetting_threshold integer,
    scout_vetting_enabled boolean,
    employer_prestige_boost boolean,
    last_ai_interpretation timestamp without time zone,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    environment_id integer
);


--
-- Name: job_vetting_requirements_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.job_vetting_requirements_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: job_vetting_requirements_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.job_vetting_requirements_id_seq OWNED BY public.job_vetting_requirements.id;


--
-- Name: parsed_email; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parsed_email (
    id integer NOT NULL,
    message_id character varying(255),
    sender_email character varying(255) NOT NULL,
    recipient_email character varying(255) NOT NULL,
    subject character varying(500),
    source_platform character varying(50),
    bullhorn_job_id integer,
    candidate_name character varying(255),
    candidate_email character varying(255),
    candidate_phone character varying(50),
    status character varying(50),
    processing_notes text,
    bullhorn_candidate_id integer,
    bullhorn_submission_id integer,
    is_duplicate_candidate boolean,
    duplicate_confidence double precision,
    resume_filename character varying(255),
    resume_file_id integer,
    received_at timestamp without time zone,
    processed_at timestamp without time zone,
    created_at timestamp without time zone,
    vetted_at timestamp without time zone,
    vetting_retry_count integer DEFAULT 0,
    environment_id integer,
    recovery_message_id character varying(255)
);


--
-- Name: parsed_email_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.parsed_email_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: parsed_email_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.parsed_email_id_seq OWNED BY public.parsed_email.id;


--
-- Name: recruiter_notification_ledger; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recruiter_notification_ledger (
    id integer NOT NULL,
    bullhorn_candidate_id integer NOT NULL,
    bullhorn_job_id integer NOT NULL,
    notification_type character varying(64) DEFAULT 'qualified'::character varying NOT NULL,
    sent_at timestamp without time zone NOT NULL,
    environment_id integer
);


--
-- Name: recruiter_notification_ledger_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.recruiter_notification_ledger_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: recruiter_notification_ledger_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.recruiter_notification_ledger_id_seq OWNED BY public.recruiter_notification_ledger.id;


--
-- Name: user; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."user" (
    id integer NOT NULL,
    username character varying(80) NOT NULL,
    email character varying(120) NOT NULL,
    password_hash character varying(256) NOT NULL,
    is_admin boolean,
    is_company_admin boolean,
    role character varying(20),
    company character varying(100),
    bullhorn_user_id integer,
    display_name character varying(255),
    subscribed_modules text,
    created_at timestamp without time zone,
    last_login timestamp without time zone,
    last_active_at timestamp without time zone,
    environment_id integer
);


--
-- Name: user_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_id_seq OWNED BY public."user".id;


--
-- Name: bullhorn_environment id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bullhorn_environment ALTER COLUMN id SET DEFAULT nextval('public.bullhorn_environment_id_seq'::regclass);


--
-- Name: bullhorn_monitor id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bullhorn_monitor ALTER COLUMN id SET DEFAULT nextval('public.bullhorn_monitor_id_seq'::regclass);


--
-- Name: candidate_fraud_assessment id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_fraud_assessment ALTER COLUMN id SET DEFAULT nextval('public.candidate_fraud_assessment_id_seq'::regclass);


--
-- Name: candidate_job_match id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_job_match ALTER COLUMN id SET DEFAULT nextval('public.candidate_job_match_id_seq'::regclass);


--
-- Name: candidate_profile_embedding id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_profile_embedding ALTER COLUMN id SET DEFAULT nextval('public.candidate_profile_embedding_id_seq'::regclass);


--
-- Name: candidate_vetting_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_vetting_log ALTER COLUMN id SET DEFAULT nextval('public.candidate_vetting_log_id_seq'::regclass);


--
-- Name: environment_brand id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.environment_brand ALTER COLUMN id SET DEFAULT nextval('public.environment_brand_id_seq'::regclass);


--
-- Name: job_embedding id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_embedding ALTER COLUMN id SET DEFAULT nextval('public.job_embedding_id_seq'::regclass);


--
-- Name: job_vetting_requirements id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_vetting_requirements ALTER COLUMN id SET DEFAULT nextval('public.job_vetting_requirements_id_seq'::regclass);


--
-- Name: parsed_email id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parsed_email ALTER COLUMN id SET DEFAULT nextval('public.parsed_email_id_seq'::regclass);


--
-- Name: recruiter_notification_ledger id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recruiter_notification_ledger ALTER COLUMN id SET DEFAULT nextval('public.recruiter_notification_ledger_id_seq'::regclass);


--
-- Name: user id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."user" ALTER COLUMN id SET DEFAULT nextval('public.user_id_seq'::regclass);


--
-- Name: bullhorn_environment bullhorn_environment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bullhorn_environment
    ADD CONSTRAINT bullhorn_environment_pkey PRIMARY KEY (id);


--
-- Name: bullhorn_monitor bullhorn_monitor_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bullhorn_monitor
    ADD CONSTRAINT bullhorn_monitor_pkey PRIMARY KEY (id);


--
-- Name: candidate_fraud_assessment candidate_fraud_assessment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_fraud_assessment
    ADD CONSTRAINT candidate_fraud_assessment_pkey PRIMARY KEY (id);


--
-- Name: candidate_job_match candidate_job_match_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_job_match
    ADD CONSTRAINT candidate_job_match_pkey PRIMARY KEY (id);


--
-- Name: candidate_profile_embedding candidate_profile_embedding_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_profile_embedding
    ADD CONSTRAINT candidate_profile_embedding_pkey PRIMARY KEY (id);


--
-- Name: candidate_vetting_log candidate_vetting_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_vetting_log
    ADD CONSTRAINT candidate_vetting_log_pkey PRIMARY KEY (id);


--
-- Name: environment_brand environment_brand_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.environment_brand
    ADD CONSTRAINT environment_brand_pkey PRIMARY KEY (id);


--
-- Name: job_embedding job_embedding_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_embedding
    ADD CONSTRAINT job_embedding_pkey PRIMARY KEY (id);


--
-- Name: job_vetting_requirements job_vetting_requirements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_vetting_requirements
    ADD CONSTRAINT job_vetting_requirements_pkey PRIMARY KEY (id);


--
-- Name: parsed_email parsed_email_message_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parsed_email
    ADD CONSTRAINT parsed_email_message_id_key UNIQUE (message_id);


--
-- Name: parsed_email parsed_email_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parsed_email
    ADD CONSTRAINT parsed_email_pkey PRIMARY KEY (id);


--
-- Name: recruiter_notification_ledger recruiter_notification_ledger_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recruiter_notification_ledger
    ADD CONSTRAINT recruiter_notification_ledger_pkey PRIMARY KEY (id);


--
-- Name: recruiter_notification_ledger uq_recruiter_notification_ledger; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recruiter_notification_ledger
    ADD CONSTRAINT uq_recruiter_notification_ledger UNIQUE (bullhorn_candidate_id, bullhorn_job_id, notification_type);


--
-- Name: user user_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."user"
    ADD CONSTRAINT user_email_key UNIQUE (email);


--
-- Name: user user_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."user"
    ADD CONSTRAINT user_pkey PRIMARY KEY (id);


--
-- Name: user user_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."user"
    ADD CONSTRAINT user_username_key UNIQUE (username);


--
-- Name: idx_cand_profile_emb_updated; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cand_profile_emb_updated ON public.candidate_profile_embedding USING btree (updated_at);


--
-- Name: idx_match_job_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_match_job_created ON public.candidate_job_match USING btree (bullhorn_job_id, created_at);


--
-- Name: idx_parsed_email_unvetted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_parsed_email_unvetted ON public.parsed_email USING btree (status, vetted_at, bullhorn_candidate_id);


--
-- Name: idx_vetting_log_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_vetting_log_status_created ON public.candidate_vetting_log USING btree (status, created_at);


--
-- Name: ix_bullhorn_environment_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bullhorn_environment_is_active ON public.bullhorn_environment USING btree (is_active);


--
-- Name: ix_bullhorn_environment_is_default; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bullhorn_environment_is_default ON public.bullhorn_environment USING btree (is_default);


--
-- Name: ix_bullhorn_environment_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_bullhorn_environment_key ON public.bullhorn_environment USING btree (key);


--
-- Name: ix_bullhorn_monitor_environment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bullhorn_monitor_environment_id ON public.bullhorn_monitor USING btree (environment_id);


--
-- Name: ix_candidate_fraud_assessment_band_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_fraud_assessment_band_created ON public.candidate_fraud_assessment USING btree (risk_band, created_at);


--
-- Name: ix_candidate_fraud_assessment_bullhorn_candidate_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_fraud_assessment_bullhorn_candidate_id ON public.candidate_fraud_assessment USING btree (bullhorn_candidate_id);


--
-- Name: ix_candidate_fraud_assessment_cand_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_fraud_assessment_cand_created ON public.candidate_fraud_assessment USING btree (bullhorn_candidate_id, created_at);


--
-- Name: ix_candidate_fraud_assessment_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_fraud_assessment_created_at ON public.candidate_fraud_assessment USING btree (created_at);


--
-- Name: ix_candidate_fraud_assessment_environment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_fraud_assessment_environment_id ON public.candidate_fraud_assessment USING btree (environment_id);


--
-- Name: ix_candidate_fraud_assessment_risk_band; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_fraud_assessment_risk_band ON public.candidate_fraud_assessment USING btree (risk_band);


--
-- Name: ix_candidate_fraud_assessment_vetting_log_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_fraud_assessment_vetting_log_id ON public.candidate_fraud_assessment USING btree (vetting_log_id);


--
-- Name: ix_candidate_job_match_bullhorn_job_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_job_match_bullhorn_job_id ON public.candidate_job_match USING btree (bullhorn_job_id);


--
-- Name: ix_candidate_job_match_environment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_job_match_environment_id ON public.candidate_job_match USING btree (environment_id);


--
-- Name: ix_candidate_job_match_vetting_log_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_job_match_vetting_log_id ON public.candidate_job_match USING btree (vetting_log_id);


--
-- Name: ix_candidate_profile_embedding_bullhorn_candidate_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_candidate_profile_embedding_bullhorn_candidate_id ON public.candidate_profile_embedding USING btree (bullhorn_candidate_id);


--
-- Name: ix_candidate_profile_embedding_environment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_profile_embedding_environment_id ON public.candidate_profile_embedding USING btree (environment_id);


--
-- Name: ix_candidate_vetting_log_bullhorn_candidate_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_vetting_log_bullhorn_candidate_id ON public.candidate_vetting_log USING btree (bullhorn_candidate_id);


--
-- Name: ix_candidate_vetting_log_candidate_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_vetting_log_candidate_email ON public.candidate_vetting_log USING btree (candidate_email);


--
-- Name: ix_candidate_vetting_log_candidate_linkedin_url; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_vetting_log_candidate_linkedin_url ON public.candidate_vetting_log USING btree (candidate_linkedin_url);


--
-- Name: ix_candidate_vetting_log_candidate_phone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_vetting_log_candidate_phone ON public.candidate_vetting_log USING btree (candidate_phone);


--
-- Name: ix_candidate_vetting_log_environment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_vetting_log_environment_id ON public.candidate_vetting_log USING btree (environment_id);


--
-- Name: ix_candidate_vetting_log_parsed_email_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_vetting_log_parsed_email_id ON public.candidate_vetting_log USING btree (parsed_email_id);


--
-- Name: ix_environment_brand_environment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_environment_brand_environment_id ON public.environment_brand USING btree (environment_id);


--
-- Name: ix_environment_brand_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_environment_brand_is_active ON public.environment_brand USING btree (is_active);


--
-- Name: ix_environment_brand_is_default; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_environment_brand_is_default ON public.environment_brand USING btree (is_default);


--
-- Name: ix_environment_brand_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_environment_brand_key ON public.environment_brand USING btree (key);


--
-- Name: ix_job_embedding_bullhorn_job_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_job_embedding_bullhorn_job_id ON public.job_embedding USING btree (bullhorn_job_id);


--
-- Name: ix_job_embedding_environment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_job_embedding_environment_id ON public.job_embedding USING btree (environment_id);


--
-- Name: ix_job_vetting_requirements_bullhorn_job_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_job_vetting_requirements_bullhorn_job_id ON public.job_vetting_requirements USING btree (bullhorn_job_id);


--
-- Name: ix_job_vetting_requirements_environment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_job_vetting_requirements_environment_id ON public.job_vetting_requirements USING btree (environment_id);


--
-- Name: ix_parsed_email_candidate_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_parsed_email_candidate_email ON public.parsed_email USING btree (candidate_email);


--
-- Name: ix_parsed_email_environment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_parsed_email_environment_id ON public.parsed_email USING btree (environment_id);


--
-- Name: ix_recruiter_notification_ledger_bullhorn_candidate_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recruiter_notification_ledger_bullhorn_candidate_id ON public.recruiter_notification_ledger USING btree (bullhorn_candidate_id);


--
-- Name: ix_recruiter_notification_ledger_bullhorn_job_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recruiter_notification_ledger_bullhorn_job_id ON public.recruiter_notification_ledger USING btree (bullhorn_job_id);


--
-- Name: ix_recruiter_notification_ledger_environment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recruiter_notification_ledger_environment_id ON public.recruiter_notification_ledger USING btree (environment_id);


--
-- Name: ix_recruiter_notification_ledger_notification_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recruiter_notification_ledger_notification_type ON public.recruiter_notification_ledger USING btree (notification_type);


--
-- Name: ix_recruiter_notification_ledger_sent_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recruiter_notification_ledger_sent_at ON public.recruiter_notification_ledger USING btree (sent_at);


--
-- Name: ix_user_environment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_environment_id ON public."user" USING btree (environment_id);


--
-- Name: uq_bullhorn_environment_single_default; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_bullhorn_environment_single_default ON public.bullhorn_environment USING btree (is_default) WHERE is_default;


--
-- Name: uq_cpe_env_candidate; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_cpe_env_candidate ON public.candidate_profile_embedding USING btree (environment_id, bullhorn_candidate_id);


--
-- Name: uq_environment_brand_single_default; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_environment_brand_single_default ON public.environment_brand USING btree (is_default) WHERE is_default;


--
-- Name: uq_je_env_job; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_je_env_job ON public.job_embedding USING btree (environment_id, bullhorn_job_id);


--
-- Name: uq_jvr_env_job; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_jvr_env_job ON public.job_vetting_requirements USING btree (environment_id, bullhorn_job_id);


--
-- Name: candidate_job_match candidate_job_match_vetting_log_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_job_match
    ADD CONSTRAINT candidate_job_match_vetting_log_id_fkey FOREIGN KEY (vetting_log_id) REFERENCES public.candidate_vetting_log(id);


--
-- Name: environment_brand environment_brand_environment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.environment_brand
    ADD CONSTRAINT environment_brand_environment_id_fkey FOREIGN KEY (environment_id) REFERENCES public.bullhorn_environment(id);


--
-- PostgreSQL database dump complete
--

\unrestrict IVylmMzPiXycrbRUGcr2SBrkxNZCzYtPVSw2zvpfgC0eWL7sFnGkClca3FeF2qD

