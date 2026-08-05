"""Tests for Scout new-requirement-spec create notify email."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


class TestRequirementsSpecNotifyConfig:
    def test_default_enabled_and_kyle_email(self, monkeypatch):
        monkeypatch.delenv('REQUIREMENTS_SPEC_NOTIFY_ENABLED', raising=False)
        monkeypatch.delenv('REQUIREMENTS_SPEC_NOTIFY_EMAIL', raising=False)
        from screening.requirements_spec_notify import notify_config

        cfg = notify_config()
        assert cfg['enabled'] is True
        assert cfg['notify_email'] == 'kroots@myticas.com'

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv('REQUIREMENTS_SPEC_NOTIFY_ENABLED', 'false')
        monkeypatch.setenv('REQUIREMENTS_SPEC_NOTIFY_EMAIL', 'ops@example.com')
        from screening.requirements_spec_notify import notify_config

        cfg = notify_config()
        assert cfg['enabled'] is False
        assert cfg['notify_email'] == 'ops@example.com'


class TestBuildNotifyMessage:
    def test_includes_job_id_title_link_and_excerpt(self):
        from screening.requirements_spec_notify import build_notify_message, bullhorn_job_url

        msg = build_notify_message(
            job_id=34967,
            job_title='Fullstack Application Developer',
            requirements='- Python 5+ years\n- AWS',
            job_location='Dallas, TX, US',
            job_work_type='On-site',
            created_at=datetime(2026, 8, 5, 18, 0, 0, tzinfo=timezone.utc),
            bh_base_url='https://cls45.bullhornstaffing.com',
        )
        assert 'Job ID: 34967' in msg
        assert 'Fullstack Application Developer' in msg
        assert 'Dallas, TX, US' in msg
        assert 'On-site' in msg
        assert '- Python 5+ years' in msg
        assert bullhorn_job_url(34967) in msg
        assert 'sanity-check' in msg.lower() or 'sanity' in msg.lower()


class TestNotifyNewRequirementsSpec:
    def test_sends_when_enabled(self, monkeypatch):
        monkeypatch.setenv('REQUIREMENTS_SPEC_NOTIFY_ENABLED', 'true')
        monkeypatch.setenv('REQUIREMENTS_SPEC_NOTIFY_EMAIL', 'kroots@myticas.com')

        mock_svc = MagicMock()
        mock_svc.send_notification_email.return_value = True

        with patch(
            'utils.bullhorn_helpers.get_email_service',
            return_value=mock_svc,
        ):
            from screening.requirements_spec_notify import notify_new_requirements_spec

            ok = notify_new_requirements_spec(
                job_id=34967,
                job_title='Dev',
                requirements='- Python',
            )

        assert ok is True
        mock_svc.send_notification_email.assert_called_once()
        kwargs = mock_svc.send_notification_email.call_args[1]
        assert kwargs['to_email'] == 'kroots@myticas.com'
        assert '34967' in kwargs['subject']
        assert kwargs['notification_type'] == 'requirements_spec_create'
        assert 'Job ID: 34967' in kwargs['message']

    def test_skips_when_disabled(self, monkeypatch):
        monkeypatch.setenv('REQUIREMENTS_SPEC_NOTIFY_ENABLED', 'off')
        mock_svc = MagicMock()

        with patch(
            'utils.bullhorn_helpers.get_email_service',
            return_value=mock_svc,
        ):
            from screening.requirements_spec_notify import notify_new_requirements_spec

            ok = notify_new_requirements_spec(
                job_id=1,
                job_title='X',
                requirements='- y',
            )

        assert ok is False
        mock_svc.send_notification_email.assert_not_called()

    def test_email_failure_does_not_raise(self, monkeypatch):
        monkeypatch.setenv('REQUIREMENTS_SPEC_NOTIFY_ENABLED', 'true')

        mock_svc = MagicMock()
        mock_svc.send_notification_email.side_effect = RuntimeError('SendGrid down')

        with patch(
            'utils.bullhorn_helpers.get_email_service',
            return_value=mock_svc,
        ):
            from screening.requirements_spec_notify import notify_new_requirements_spec

            ok = notify_new_requirements_spec(
                job_id=2,
                job_title='Y',
                requirements='- z',
            )

        assert ok is False


class TestSaveAiInterpretedRequirementsNotify:
    """Hook: create notifies; update does not; notify exceptions do not break save."""

    def _mixin_instance(self):
        from screening.job_management import JobManagementMixin

        class _Svc(JobManagementMixin):
            pass

        return _Svc()

    def test_notify_on_create(self, app, monkeypatch):
        monkeypatch.setenv('REQUIREMENTS_SPEC_NOTIFY_ENABLED', 'true')

        with app.app_context():
            from app import db
            from models import JobVettingRequirements

            job_id = 990001
            JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete()
            db.session.commit()

            with patch(
                'screening.requirements_spec_notify.notify_new_requirements_spec'
            ) as mock_notify:
                mock_notify.return_value = True
                svc = self._mixin_instance()
                svc._save_ai_interpreted_requirements(
                    job_id,
                    'New Spec Job',
                    '- Must know Python\n- AWS preferred',
                    'Austin, TX, US',
                    'Remote',
                    description_hash='a' * 64,
                )

            row = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
            assert row is not None
            assert 'Python' in (row.ai_interpreted_requirements or '')
            mock_notify.assert_called_once()
            call_kw = mock_notify.call_args[1]
            assert call_kw['job_id'] == job_id
            assert call_kw['job_title'] == 'New Spec Job'

            JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete()
            db.session.commit()

    def test_no_notify_on_update(self, app, monkeypatch):
        monkeypatch.setenv('REQUIREMENTS_SPEC_NOTIFY_ENABLED', 'true')

        with app.app_context():
            from app import db
            from models import JobVettingRequirements

            job_id = 990002
            JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete()
            db.session.add(JobVettingRequirements(
                bullhorn_job_id=job_id,
                job_title='Existing',
                ai_interpreted_requirements='- Old req',
                last_ai_interpretation=datetime.utcnow(),
            ))
            db.session.commit()

            with patch(
                'screening.requirements_spec_notify.notify_new_requirements_spec'
            ) as mock_notify:
                svc = self._mixin_instance()
                svc._save_ai_interpreted_requirements(
                    job_id,
                    'Existing Updated',
                    '- New req after regen',
                    None,
                    None,
                    description_hash='b' * 64,
                )

            mock_notify.assert_not_called()
            row = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
            assert 'New req' in (row.ai_interpreted_requirements or '')

            JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete()
            db.session.commit()

    def test_notify_exception_does_not_raise_or_rollback(self, app, monkeypatch):
        monkeypatch.setenv('REQUIREMENTS_SPEC_NOTIFY_ENABLED', 'true')

        with app.app_context():
            from app import db
            from models import JobVettingRequirements

            job_id = 990003
            JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete()
            db.session.commit()

            with patch(
                'screening.requirements_spec_notify.notify_new_requirements_spec',
                side_effect=RuntimeError('boom'),
            ):
                svc = self._mixin_instance()
                # Must not raise
                svc._save_ai_interpreted_requirements(
                    job_id,
                    'Fail Soft Job',
                    '- Still saved',
                    None,
                    None,
                )

            row = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
            assert row is not None
            assert 'Still saved' in (row.ai_interpreted_requirements or '')

            JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete()
            db.session.commit()
