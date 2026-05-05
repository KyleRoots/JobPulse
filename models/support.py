"""Scout Support tickets, conversations, attachments, actions, and Knowledge Hub."""
import json
from datetime import datetime
from extensions import db


class SupportContact(db.Model):
    __tablename__ = 'support_contact'
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    brand = db.Column(db.String(100), nullable=False, default='Myticas')
    department = db.Column(db.String(100), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('email', 'brand', name='uq_support_contact_email_brand'),
    )

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'

    def to_dict(self):
        return {
            'id': self.id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'full_name': self.full_name,
            'email': self.email,
            'brand': self.brand,
            'department': self.department or '',
        }

    def __repr__(self):
        return f'<SupportContact {self.id}: {self.full_name} ({self.brand})>'


class SupportTicket(db.Model):
    __tablename__ = 'support_ticket'

    id = db.Column(db.Integer, primary_key=True)
    ticket_number = db.Column(db.String(20), unique=True, nullable=False, index=True)
    category = db.Column(db.String(50), nullable=False)
    subject = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text, nullable=False)
    priority = db.Column(db.String(20), nullable=False, default='medium')
    brand = db.Column(db.String(20), nullable=False, default='Myticas')

    status = db.Column(db.String(50), nullable=False, default='new')

    submitter_name = db.Column(db.String(255), nullable=False)
    submitter_email = db.Column(db.String(255), nullable=False, index=True)
    submitter_department = db.Column(db.String(100), nullable=True)

    admin_email = db.Column(db.String(255), nullable=False, default='kroots@myticas.com')

    ai_understanding = db.Column(db.Text, nullable=True)
    proposed_solution = db.Column(db.Text, nullable=True)
    execution_proof = db.Column(db.Text, nullable=True)
    execution_attempts = db.Column(db.Integer, nullable=False, default=0)
    execution_history = db.Column(db.Text, nullable=True)
    escalation_reason = db.Column(db.Text, nullable=True)

    user_approved_at = db.Column(db.DateTime, nullable=True)
    admin_approved_at = db.Column(db.DateTime, nullable=True)
    admin_response = db.Column(db.Text, nullable=True)

    resolution_note = db.Column(db.Text, nullable=True)
    resolved_by = db.Column(db.String(255), nullable=True)

    last_message_id = db.Column(db.String(255), nullable=True)
    thread_message_id = db.Column(db.String(255), nullable=True)
    last_reminder_at = db.Column(db.DateTime, nullable=True)
    reminder_count = db.Column(db.Integer, nullable=False, default=0)

    # Send-to-Agent-for-Build workflow (platform tickets only)
    sent_to_build_at = db.Column(db.DateTime, nullable=True)
    sent_to_build_by = db.Column(db.String(255), nullable=True)
    build_summary = db.Column(db.Text, nullable=True)
    deployed_at = db.Column(db.DateTime, nullable=True)
    deployed_by = db.Column(db.String(255), nullable=True)
    deploy_commit_link = db.Column(db.String(500), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)

    conversations = db.relationship('SupportConversation', backref='ticket', lazy='dynamic',
                                     cascade='all, delete-orphan',
                                     order_by='SupportConversation.created_at')
    actions = db.relationship('SupportAction', backref='ticket', lazy='dynamic',
                               cascade='all, delete-orphan',
                               order_by='SupportAction.executed_at')
    attachments = db.relationship('SupportAttachment', backref='ticket', lazy='dynamic',
                                   cascade='all, delete-orphan',
                                   order_by='SupportAttachment.created_at')

    __table_args__ = (
        db.Index('idx_support_status', 'status'),
        db.Index('idx_support_submitter_status', 'submitter_email', 'status'),
        db.Index('idx_support_admin_status', 'admin_email', 'status'),
    )

    VALID_STATUSES = [
        'new', 'acknowledged', 'clarifying', 'solution_proposed',
        'awaiting_user_approval', 'awaiting_admin_approval', 'admin_handling',
        'admin_clarifying', 'approved', 'executing', 'retrying', 'execution_failed',
        'in_development',
        'completed', 'on_hold', 'closed', 'escalated'
    ]

    @property
    def parsed_ai_understanding(self):
        """Safely parse ai_understanding JSON field; returns dict or None."""
        if not self.ai_understanding:
            return None
        try:
            return json.loads(self.ai_understanding)
        except (ValueError, TypeError):
            return None

    @property
    def parsed_proposed_solution(self):
        """Safely parse proposed_solution JSON field; returns dict or None."""
        if not self.proposed_solution:
            return None
        try:
            return json.loads(self.proposed_solution)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def generate_ticket_number():
        year = datetime.utcnow().year
        prefix = f'SS-{year}-'
        latest = SupportTicket.query.filter(
            SupportTicket.ticket_number.like(f'{prefix}%')
        ).order_by(SupportTicket.id.desc()).first()
        if latest:
            try:
                seq = int(latest.ticket_number.split('-')[-1]) + 1
            except (ValueError, IndexError):
                seq = 1
        else:
            seq = 1
        return f'{prefix}{seq:04d}'

    def __repr__(self):
        return f'<SupportTicket {self.ticket_number} status={self.status}>'


class SupportAttachment(db.Model):
    __tablename__ = 'support_attachment'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('support_ticket.id'), nullable=False, index=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('support_conversation.id'), nullable=True, index=True)
    filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(100), nullable=False, default='application/octet-stream')
    file_data = db.Column(db.LargeBinary, nullable=False)
    file_size = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    conversation = db.relationship('SupportConversation', backref='attachments', foreign_keys=[conversation_id])

    def __repr__(self):
        return f'<SupportAttachment {self.id} ticket={self.ticket_id} file={self.filename}>'

    @property
    def is_image(self):
        return self.content_type.startswith('image/')


class SupportConversation(db.Model):
    __tablename__ = 'support_conversation'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('support_ticket.id'), nullable=False, index=True)
    direction = db.Column(db.String(20), nullable=False)
    sender_email = db.Column(db.String(255), nullable=False)
    recipient_email = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(500), nullable=True)
    body = db.Column(db.Text, nullable=False)
    message_id = db.Column(db.String(255), nullable=True)
    email_type = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f'<SupportConversation {self.id} ticket={self.ticket_id} dir={self.direction}>'


class SupportAction(db.Model):
    __tablename__ = 'support_action'

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('support_ticket.id'), nullable=False, index=True)
    action_type = db.Column(db.String(50), nullable=False)
    entity_type = db.Column(db.String(50), nullable=True)
    entity_id = db.Column(db.Integer, nullable=True)
    field_name = db.Column(db.String(100), nullable=True)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    summary = db.Column(db.Text, nullable=True)
    success = db.Column(db.Boolean, nullable=False, default=True)
    error_message = db.Column(db.Text, nullable=True)
    executed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f'<SupportAction {self.id} ticket={self.ticket_id} type={self.action_type}>'


class KnowledgeDocument(db.Model):
    __tablename__ = 'knowledge_document'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    filename = db.Column(db.String(255), nullable=True)
    doc_type = db.Column(db.String(50), nullable=False, default='uploaded')
    category = db.Column(db.String(100), nullable=True, index=True)
    description = db.Column(db.Text, nullable=True)
    source_ticket_id = db.Column(db.Integer, db.ForeignKey('support_ticket.id'), nullable=True)
    raw_text = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='active')
    uploaded_by = db.Column(db.String(255), nullable=True)
    onedrive_item_id = db.Column(db.String(255), nullable=True, index=True)
    onedrive_etag = db.Column(db.String(255), nullable=True)
    onedrive_folder_id = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    entries = db.relationship('KnowledgeEntry', backref='document', lazy='dynamic',
                              cascade='all, delete-orphan')
    source_ticket = db.relationship('SupportTicket', backref='knowledge_documents')

    __table_args__ = (
        db.Index('idx_knowledge_doc_status', 'status'),
        db.Index('idx_knowledge_doc_type', 'doc_type'),
    )

    def __repr__(self):
        return f'<KnowledgeDocument {self.id} "{self.title[:40]}">'


class KnowledgeEntry(db.Model):
    __tablename__ = 'knowledge_entry'

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('knowledge_document.id'), nullable=False, index=True)
    chunk_index = db.Column(db.Integer, nullable=False, default=0)
    content = db.Column(db.Text, nullable=False)
    content_hash = db.Column(db.String(64), nullable=True, index=True)
    embedding_vector = db.Column(db.Text, nullable=True)
    embedding_model = db.Column(db.String(50), nullable=True, default='text-embedding-3-large')
    metadata_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_knowledge_entry_doc_chunk', 'document_id', 'chunk_index'),
    )

    def __repr__(self):
        return f'<KnowledgeEntry {self.id} doc={self.document_id} chunk={self.chunk_index}>'
