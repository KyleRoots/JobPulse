import json
import logging
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user

vetting_sandbox_bp = Blueprint('vetting_sandbox', __name__)
logger = logging.getLogger(__name__)


def _require_super_admin():
    if not current_user.is_authenticated or not current_user.is_admin:
        abort(403)


@vetting_sandbox_bp.route('/vetting-sandbox')
@login_required
def sandbox_page():
    _require_super_admin()
    return render_template('vetting_sandbox.html', active_page='vetting_sandbox')


@vetting_sandbox_bp.route('/vetting-sandbox/jobs')
@login_required
def list_jobs():
    _require_super_admin()
    from models import JobVettingRequirements
    reqs = JobVettingRequirements.query.order_by(
        JobVettingRequirements.updated_at.desc()
    ).limit(100).all()
    jobs = [{
        'id': r.bullhorn_job_id,
        'title': r.job_title or f'Job {r.bullhorn_job_id}',
        'location': r.job_location or '',
        'work_type': r.job_work_type or '',
        'ai_requirements': r.ai_interpreted_requirements or '',
        'custom_requirements': r.custom_requirements or '',
        'threshold': r.vetting_threshold,
    } for r in reqs]
    return jsonify({'jobs': jobs})


@vetting_sandbox_bp.route('/vetting-sandbox/screen', methods=['POST'])
@login_required
def run_screening():
    _require_super_admin()
    from app import db
    from models import CandidateVettingLog, CandidateJobMatch, VettingConfig, JobVettingRequirements

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    resume_text = (data.get('resume_text') or '').strip()
    candidate_name = (data.get('candidate_name') or 'Test Candidate').strip()
    candidate_email = (data.get('candidate_email') or '').strip()

    job_source = data.get('job_source', 'custom')
    if job_source == 'existing':
        job_id = data.get('job_id')
        if not job_id:
            return jsonify({'error': 'No job ID provided'}), 400
        req = JobVettingRequirements.query.filter_by(bullhorn_job_id=int(job_id)).first()
        job = {
            'id': int(job_id),
            'title': req.job_title if req else f'Job {job_id}',
            'publicDescription': req.ai_interpreted_requirements if req else '',
            'address': {'city': '', 'state': ''},
        }
        prefetched_reqs = req.custom_requirements or req.ai_interpreted_requirements if req else None
    else:
        job = {
            'id': 0,
            'title': data.get('job_title', 'Test Job'),
            'publicDescription': data.get('job_description', ''),
            'address': {'city': data.get('job_location', ''), 'state': ''},
        }
        prefetched_reqs = data.get('job_requirements') or None

    if not resume_text:
        return jsonify({'error': 'Resume text is required'}), 400

    try:
        from candidate_vetting_service import CandidateVettingService
        svc = CandidateVettingService(bullhorn_service=None)

        global_reqs = VettingConfig.get_value('global_custom_requirements', '')
        model = VettingConfig.get_value('layer2_model', 'gpt-4o')

        result = svc.analyze_candidate_job_match(
            resume_text=resume_text,
            job=job,
            prefetched_requirements=prefetched_reqs,
            model_override=model,
            prefetched_global_requirements=global_reqs
        )

        threshold = int(VettingConfig.get_value('match_threshold', '80') or 80)
        if job_source == 'existing' and req and req.vetting_threshold:
            threshold = req.vetting_threshold

        score = result.get('match_score', 0)
        is_qualified = score >= threshold

        vetting_log = CandidateVettingLog(
            bullhorn_candidate_id=0,
            candidate_name=candidate_name,
            candidate_email=candidate_email,
            applied_job_id=job.get('id', 0),
            applied_job_title=job.get('title', ''),
            resume_text=resume_text,
            status='completed',
            is_qualified=is_qualified,
            highest_match_score=score,
            total_jobs_matched=1 if is_qualified else 0,
            is_sandbox=True,
            detected_at=datetime.utcnow(),
            analyzed_at=datetime.utcnow(),
        )
        db.session.add(vetting_log)
        db.session.flush()

        match = CandidateJobMatch(
            vetting_log_id=vetting_log.id,
            bullhorn_job_id=job.get('id', 0),
            job_title=job.get('title', ''),
            job_location=data.get('job_location', ''),
            match_score=score,
            is_qualified=is_qualified,
            is_applied_job=True,
            match_summary=result.get('match_summary', ''),
            skills_match=result.get('skills_match', ''),
            experience_match=result.get('experience_match', ''),
            gaps_identified=result.get('gaps_identified', ''),
        )
        db.session.add(match)
        db.session.commit()

        return jsonify({
            'success': True,
            'vetting_log_id': vetting_log.id,
            'match_id': match.id,
            'score': score,
            'threshold': threshold,
            'is_qualified': is_qualified,
            'match_summary': result.get('match_summary', ''),
            'skills_match': result.get('skills_match', ''),
            'experience_match': result.get('experience_match', ''),
            'gaps_identified': result.get('gaps_identified', ''),
        })

    except Exception as e:
        logger.error(f"Sandbox screening error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@vetting_sandbox_bp.route('/vetting-sandbox/generate-outreach', methods=['POST'])
@login_required
def generate_outreach():
    _require_super_admin()
    from app import db
    from models import CandidateVettingLog, CandidateJobMatch, ScoutVettingSession
    from scout_vetting_service import ScoutVettingService
    from email_service import EmailService

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    vetting_log_id = data.get('vetting_log_id')
    match_id = data.get('match_id')

    if not vetting_log_id or not match_id:
        return jsonify({'error': 'Missing vetting_log_id or match_id'}), 400

    vetting_log = CandidateVettingLog.query.get(vetting_log_id)
    match = CandidateJobMatch.query.get(match_id)

    if not vetting_log or not match or not vetting_log.is_sandbox:
        return jsonify({'error': 'Invalid sandbox record'}), 400

    try:
        email_svc = EmailService()
        scout_svc = ScoutVettingService(email_service=email_svc, bullhorn_service=None)

        session = ScoutVettingSession(
            vetting_log_id=vetting_log.id,
            candidate_job_match_id=match.id,
            bullhorn_candidate_id=0,
            candidate_email=vetting_log.candidate_email or 'test@example.com',
            candidate_name=vetting_log.candidate_name or 'Test Candidate',
            bullhorn_job_id=match.bullhorn_job_id or 0,
            job_title=match.job_title or '',
            status='pending',
            is_sandbox=True,
        )
        db.session.add(session)
        db.session.flush()

        questions = scout_svc.generate_vetting_questions(session)
        session.vetting_questions_json = json.dumps(questions)
        db.session.commit()

        email_html = scout_svc._build_outreach_email(session, questions)
        subject = scout_svc._build_subject(session, is_initial=True)

        return jsonify({
            'success': True,
            'session_id': session.id,
            'questions': questions,
            'email_subject': subject,
            'email_html': email_html,
        })

    except Exception as e:
        logger.error(f"Sandbox outreach generation error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@vetting_sandbox_bp.route('/vetting-sandbox/send-outreach', methods=['POST'])
@login_required
def send_outreach():
    _require_super_admin()
    from app import db
    from models import ScoutVettingSession, VettingConversationTurn
    from scout_vetting_service import ScoutVettingService, SCOUT_VETTING_REPLY_TO, SCOUT_VETTING_FROM_NAME
    from email_service import EmailService

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    session_id = data.get('session_id')
    test_email = (data.get('test_email') or '').strip()

    if not session_id or not test_email:
        return jsonify({'error': 'Missing session_id or test_email'}), 400

    session = ScoutVettingSession.query.get(session_id)
    if not session or not session.is_sandbox:
        return jsonify({'error': 'Invalid sandbox session'}), 400

    try:
        email_svc = EmailService()
        scout_svc = ScoutVettingService(email_service=email_svc, bullhorn_service=None)

        questions = json.loads(session.vetting_questions_json or '[]')
        email_html = scout_svc._build_outreach_email(session, questions)
        subject = scout_svc._build_subject(session, is_initial=True)

        result = email_svc.send_html_email(
            to_email=test_email,
            subject=f"[SANDBOX] {subject}",
            html_content=email_html,
            notification_type='scout_vetting_sandbox',
            reply_to=SCOUT_VETTING_REPLY_TO,
            from_name=SCOUT_VETTING_FROM_NAME,
        )

        success = result is True or (isinstance(result, dict) and result.get('success', False))

        if success:
            session.status = 'outreach_sent'
            session.last_outreach_at = datetime.utcnow()
            session.current_turn = 1

            turn = VettingConversationTurn(
                session_id=session.id,
                turn_number=1,
                direction='outbound',
                email_subject=subject,
                email_body=email_html,
                questions_asked_json=json.dumps(questions),
            )
            db.session.add(turn)
            db.session.commit()

        return jsonify({
            'success': success,
            'sent_to': test_email,
            'message': f'Outreach email sent to {test_email}' if success else 'Email send failed',
        })

    except Exception as e:
        logger.error(f"Sandbox send outreach error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@vetting_sandbox_bp.route('/vetting-sandbox/simulate-reply', methods=['POST'])
@login_required
def simulate_reply():
    _require_super_admin()
    from app import db
    from models import ScoutVettingSession, VettingConversationTurn
    from scout_vetting_service import ScoutVettingService
    from email_service import EmailService

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    session_id = data.get('session_id')
    reply_text = (data.get('reply_text') or '').strip()

    if not session_id or not reply_text:
        return jsonify({'error': 'Missing session_id or reply_text'}), 400

    session = ScoutVettingSession.query.get(session_id)
    if not session or not session.is_sandbox:
        return jsonify({'error': 'Invalid sandbox session'}), 400

    try:
        email_svc = EmailService()
        scout_svc = ScoutVettingService(email_service=email_svc, bullhorn_service=None)

        session.last_reply_at = datetime.utcnow()
        session.follow_up_count = 0
        if session.status == 'outreach_sent':
            session.status = 'in_progress'

        classification = scout_svc._classify_reply(session, reply_text)
        intent = classification.get('intent', 'unknown')
        reasoning = classification.get('reasoning', '')
        answers = classification.get('answers_extracted', {})

        session.current_turn = (session.current_turn or 0) + 1
        inbound_turn = VettingConversationTurn(
            session_id=session.id,
            turn_number=session.current_turn,
            direction='inbound',
            email_subject='[Simulated Reply]',
            email_body=reply_text,
            ai_intent=intent,
            ai_reasoning=reasoning,
            answers_extracted_json=json.dumps(answers) if answers else None,
        )
        db.session.add(inbound_turn)

        existing_answers = json.loads(session.answered_questions_json or '{}')
        existing_answers.update(answers)
        session.answered_questions_json = json.dumps(existing_answers)

        questions = json.loads(session.vetting_questions_json or '[]')
        answered_count = len(existing_answers)
        all_answered = answered_count >= len(questions)
        max_turns_reached = session.current_turn >= session.max_turns

        follow_up_html = None
        follow_up_questions = []

        if intent == 'decline':
            session.status = 'declined'
        elif intent in ('unrelated', 'spam', 'out_of_office'):
            pass
        elif all_answered or max_turns_reached:
            session.status = 'ready_to_finalize'
        else:
            unanswered = [q for i, q in enumerate(questions) if str(i) not in existing_answers and q not in existing_answers.values()]
            follow_up_questions = unanswered[:3]
            if hasattr(scout_svc, '_build_followup_email'):
                try:
                    follow_up_html = scout_svc._build_followup_email(session, follow_up_questions)
                except Exception:
                    pass

            session.current_turn += 1
            outbound_turn = VettingConversationTurn(
                session_id=session.id,
                turn_number=session.current_turn,
                direction='outbound',
                email_subject='[Sandbox Follow-up]',
                email_body=follow_up_html or f"Follow-up questions: {json.dumps(follow_up_questions)}",
                questions_asked_json=json.dumps(follow_up_questions),
            )
            db.session.add(outbound_turn)

        db.session.commit()

        turns = VettingConversationTurn.query.filter_by(
            session_id=session.id
        ).order_by(VettingConversationTurn.turn_number.asc()).all()

        transcript = [{
            'turn': t.turn_number,
            'direction': t.direction,
            'body': t.email_body[:500] if t.direction == 'outbound' else t.email_body,
            'intent': t.ai_intent,
            'reasoning': t.ai_reasoning,
        } for t in turns]

        return jsonify({
            'success': True,
            'intent': intent,
            'reasoning': reasoning,
            'answers_extracted': answers,
            'total_answered': answered_count,
            'total_questions': len(questions),
            'all_answered': all_answered,
            'max_turns_reached': max_turns_reached,
            'session_status': session.status,
            'follow_up_questions': follow_up_questions,
            'follow_up_html': follow_up_html,
            'transcript': transcript,
        })

    except Exception as e:
        logger.error(f"Sandbox simulate reply error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@vetting_sandbox_bp.route('/vetting-sandbox/finalize', methods=['POST'])
@login_required
def finalize():
    _require_super_admin()
    from app import db
    from models import ScoutVettingSession, VettingConversationTurn
    from scout_vetting_service import ScoutVettingService
    from email_service import EmailService

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'error': 'Missing session_id'}), 400

    session = ScoutVettingSession.query.get(session_id)
    if not session or not session.is_sandbox:
        return jsonify({'error': 'Invalid sandbox session'}), 400

    try:
        email_svc = EmailService()
        scout_svc = ScoutVettingService(email_service=email_svc, bullhorn_service=None)

        outcome = scout_svc._generate_outcome(session)
        session.outcome_summary = outcome.get('summary', '')
        session.outcome_score = outcome.get('score', 0.0)
        session.status = outcome.get('recommendation', 'qualified')

        db.session.commit()

        turns = VettingConversationTurn.query.filter_by(
            session_id=session.id
        ).order_by(VettingConversationTurn.turn_number.asc()).all()

        transcript = [{
            'turn': t.turn_number,
            'direction': t.direction,
            'body': t.email_body,
            'intent': t.ai_intent,
            'reasoning': t.ai_reasoning,
        } for t in turns]

        return jsonify({
            'success': True,
            'outcome_score': session.outcome_score,
            'outcome_summary': session.outcome_summary,
            'recommendation': session.status,
            'transcript': transcript,
        })

    except Exception as e:
        logger.error(f"Sandbox finalize error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@vetting_sandbox_bp.route('/vetting-sandbox/cleanup', methods=['POST'])
@login_required
def cleanup():
    _require_super_admin()
    from app import db
    from models import CandidateVettingLog, CandidateJobMatch, ScoutVettingSession, VettingConversationTurn

    try:
        sandbox_sessions = ScoutVettingSession.query.filter_by(is_sandbox=True).all()
        turn_count = 0
        for s in sandbox_sessions:
            turns = VettingConversationTurn.query.filter_by(session_id=s.id).delete()
            turn_count += turns
        session_count = ScoutVettingSession.query.filter_by(is_sandbox=True).delete()

        sandbox_logs = CandidateVettingLog.query.filter_by(is_sandbox=True).all()
        match_count = 0
        for log in sandbox_logs:
            matches = CandidateJobMatch.query.filter_by(vetting_log_id=log.id).delete()
            match_count += matches
        log_count = CandidateVettingLog.query.filter_by(is_sandbox=True).delete()

        db.session.commit()

        return jsonify({
            'success': True,
            'deleted': {
                'conversation_turns': turn_count,
                'sessions': session_count,
                'matches': match_count,
                'vetting_logs': log_count,
            },
        })

    except Exception as e:
        logger.error(f"Sandbox cleanup error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
