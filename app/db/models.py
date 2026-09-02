import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Column,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.models.schemas import (
    AppRole,
    FeedbackRating,
    KnowledgePersona,
    KnowledgeType,
    MessageSender,
    MessageType,
    Persona,
)


class Base(DeclarativeBase):
    pass


# Bare stub for Supabase's own auth.users table -- exists only so
# profiles.id's foreign key can resolve it structurally. We don't own this
# table, never map/query it via the ORM, and migrations/env.py explicitly
# excludes the `auth` schema from autogenerate comparison so Alembic never
# tries to create/alter/drop it.
auth_users = Table(
    "users",
    Base.metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    schema="auth",
)

# create_type=False: the app_role enum already exists in Postgres (created by
# the baseline migration) -- SQLAlchemy should reference it, never try to
# create/drop it itself.
app_role_pg_enum = SAEnum(AppRole, name="app_role", create_type=False)

# These enums are new -- unlike app_role, they don't already exist in
# Postgres, so create_type defaults to True and the migration is expected to
# create the types itself (see migrations/versions for the revision that
# adds these tables).
persona_pg_enum = SAEnum(Persona, name="persona")
knowledge_persona_pg_enum = SAEnum(KnowledgePersona, name="knowledge_persona")
knowledge_type_pg_enum = SAEnum(KnowledgeType, name="knowledge_type")
message_sender_pg_enum = SAEnum(MessageSender, name="message_sender")
feedback_rating_pg_enum = SAEnum(FeedbackRating, name="feedback_rating")
message_type_pg_enum = SAEnum(MessageType, name="message_type")


class Profile(Base):
    """Mirrors public.profiles -- see supabase/migrations/0001_auth_and_roles.sql
    (historical raw-SQL record of this schema) / migrations/versions baseline
    (the actual Alembic-tracked source of truth going forward)."""

    __tablename__ = "profiles"
    # No explicit schema="public": Postgres's search_path defaults to public
    # for this connection, and SQLAlchemy's reflection reports tables in the
    # default schema as schema=None -- setting "public" explicitly here
    # would make autogenerate see the model and the reflected DB table as
    # schema='public' vs schema=None and misreport a spurious diff.
    __table_args__ = (
        Index(
            "profiles_username_unique",
            "username",
            unique=True,
            postgresql_where=text("username IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(auth_users.c.id, ondelete="CASCADE", name="profiles_id_fkey"),
        primary_key=True,
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Display-only handle, self-set from the Settings page (PATCH /auth/me
    # in app/routers/auth.py) -- never used for login (that's always email
    # via Supabase). Nullable (existing rows have none) and unique when set;
    # a partial unique index (below) lets multiple rows stay NULL at once,
    # which a plain unique constraint would not allow.
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Self-service signups (POST /auth/signup) start unapproved and can't
    # log in until an admin approves them (POST /admin/users/{id}/approve)
    # -- see login()'s approval check in app/routers/auth.py. Defaults to
    # false so the on_auth_user_created trigger's insert (which doesn't set
    # this column) always starts a new account pending.
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", foreign_keys="UserRole.user_id"
    )


class UserRole(Base):
    """Mirrors public.user_roles. Soft-revoke (revoked_at/revoked_by), with a
    partial unique index enforcing one *active* (revoked_at IS NULL) row per
    (user_id, role) -- re-granting after a revoke inserts a fresh row rather
    than resurrecting the old one."""

    __tablename__ = "user_roles"
    __table_args__ = (
        Index(
            "user_roles_active_unique",
            "user_id",
            "role",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("user_roles_user_id_idx", "user_id"),
        Index(
            "user_roles_active_role_idx",
            "role",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        CheckConstraint(
            "(revoked_at is null) = (revoked_by is null)",
            name="revoked_by_requires_revoked_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE", name="user_roles_user_id_fkey"),
        nullable=False,
    )
    role: Mapped[AppRole] = mapped_column(app_role_pg_enum, nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", name="user_roles_granted_by_fkey"),
        nullable=True,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", name="user_roles_revoked_by_fkey"),
        nullable=True,
    )

    user: Mapped["Profile"] = relationship(back_populates="roles", foreign_keys=[user_id])


class KnowledgeEntry(Base):
    """Mirrors public.knowledge_entries -- the searchable knowledge base for
    both personas (see migrations/versions for the revision that adds this
    table). One flexible table rather than split per document type, since
    everything is searched together via embedding similarity."""

    __tablename__ = "knowledge_entries"
    __table_args__ = (
        Index("knowledge_entries_persona_idx", "persona"),
        Index("knowledge_entries_type_idx", "type"),
        Index(
            "knowledge_entries_embedding_hnsw_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    persona: Mapped[KnowledgePersona] = mapped_column(knowledge_persona_pg_enum, nullable=False)
    type: Mapped[KnowledgeType] = mapped_column(knowledge_type_pg_enum, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Python attr is metadata_ (not metadata) -- that name is reserved by
    # DeclarativeBase for the table's MetaData object. DB column is still
    # named "metadata".
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Nullable: embeddings are generated by an async job after the row is
    # written, not atomically with it.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # SET NULL, not CASCADE: deleting an employee's account shouldn't delete
    # the company knowledge base content they authored.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            auth_users.c.id, ondelete="SET NULL", name="knowledge_entries_created_by_fkey"
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Conversation(Base):
    """Mirrors public.conversations -- one chat session between a user and
    the AI."""

    __tablename__ = "conversations"
    __table_args__ = (Index("conversations_user_id_idx", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(auth_users.c.id, ondelete="CASCADE", name="conversations_user_id_fkey"),
        nullable=False,
    )
    persona: Mapped[Persona] = mapped_column(persona_pg_enum, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Rolling long-term memory summary, updated by a background task after
    # each assistant reply and carried forward as the seed for the user's
    # next new conversation (see create_conversation() in chat.py).
    memory_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    """Mirrors public.messages -- every message inside a conversation."""

    __tablename__ = "messages"
    __table_args__ = (Index("messages_conversation_id_idx", "conversation_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE", name="messages_conversation_id_fkey"),
        nullable=False,
    )
    sender: Mapped[MessageSender] = mapped_column(message_sender_pg_enum, nullable=False)
    message_type: Mapped[MessageType | None] = mapped_column(message_type_pg_enum, nullable=True)
    # Scaffolding rows the pipeline persists for later turns to read back
    # (e.g. the enriched-situation seed message a direct pitch-intent turn
    # writes so _load_last_strategy_message() has grounding context) but
    # that were never meant to be shown to the user as an answer in their
    # own right. GET .../messages excludes these; the pipeline still reads
    # them directly by DB query.
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # none_as_null=True: without it, JSONB's default write behavior stores a
    # Python None as the JSON scalar `null` rather than SQL NULL, which
    # silently defeats every `.isnot(None)` filter used elsewhere to find
    # "the last message with real pipeline_metadata" (e.g.
    # _load_prior_pipeline_context in app/routers/chat.py).
    pipeline_metadata: Mapped[dict | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    sources: Mapped[list["MessageSource"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class MessageSource(Base):
    """Mirrors public.message_sources -- join table linking an AI answer to
    the knowledge entries it cited."""

    __tablename__ = "message_sources"
    __table_args__ = (
        Index("message_sources_message_id_idx", "message_id"),
        Index("message_sources_knowledge_entry_id_idx", "knowledge_entry_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE", name="message_sources_message_id_fkey"),
        nullable=False,
    )
    knowledge_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "knowledge_entries.id",
            ondelete="CASCADE",
            name="message_sources_knowledge_entry_id_fkey",
        ),
        nullable=False,
    )
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    message: Mapped["Message"] = relationship(back_populates="sources")


class Feedback(Base):
    """Mirrors public.feedback -- Useful / Not Useful rating on an AI
    message."""

    __tablename__ = "feedback"
    __table_args__ = (
        Index("feedback_message_id_idx", "message_id"),
        Index("feedback_user_id_idx", "user_id"),
        # Lets a user change their rating on a message via upsert
        # (ON CONFLICT (message_id, user_id) DO UPDATE) instead of
        # accumulating duplicate feedback rows.
        Index("feedback_message_user_unique", "message_id", "user_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE", name="feedback_message_id_fkey"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(auth_users.c.id, ondelete="CASCADE", name="feedback_user_id_fkey"),
        nullable=False,
    )
    rating: Mapped[FeedbackRating] = mapped_column(feedback_rating_pg_enum, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WebsiteAnalysis(Base):
    """Mirrors public.website_analyses -- output of the website analyzer
    tool (Support Customer persona only)."""

    __tablename__ = "website_analyses"
    __table_args__ = (Index("website_analyses_conversation_id_idx", "conversation_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "conversations.id",
            ondelete="CASCADE",
            name="website_analyses_conversation_id_fkey",
        ),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LlmCallLog(Base):
    """Mirrors public.llm_call_logs -- one row per individual LLM API call
    made anywhere in the /chat pipeline (see app/services/usage_tracking.py),
    letting cost/token usage be broken down per pipeline step, not just per
    user turn."""

    __tablename__ = "llm_call_logs"
    __table_args__ = (
        Index("llm_call_logs_conversation_id_idx", "conversation_id"),
        Index("llm_call_logs_message_id_idx", "message_id"),
        Index("llm_call_logs_user_id_idx", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE", name="llm_call_logs_conversation_id_fkey"),
        nullable=False,
    )
    # Nullable: a handful of early-return pipeline paths record usage before
    # any assistant Message row is created for that turn.
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE", name="llm_call_logs_message_id_fkey"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(auth_users.c.id, ondelete="CASCADE", name="llm_call_logs_user_id_fkey"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    call_name: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PitchEvaluation(Base):
    """Mirrors public.pitch_evaluations -- one row per generated pitch
    Message, holding the LLM-as-judge audit of that pitch against the W2R
    RAG Addendum rubric (see PITCH_EVALUATION_PROMPT / evaluate_pitch() in
    app/services/llm.py). Written by a background task after the pitch is
    already persisted and shown to the user (see _evaluate_pitch_background
    in app/routers/chat.py) -- a missing row for a given message just means
    the background evaluation hasn't finished (or failed) yet, not that the
    pitch itself is invalid."""

    __tablename__ = "pitch_evaluations"
    __table_args__ = (
        Index("pitch_evaluations_message_id_idx", "message_id"),
        Index("pitch_evaluations_conversation_id_idx", "conversation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE", name="pitch_evaluations_message_id_fkey"),
        nullable=False,
    )
    # Denormalized alongside message_id so conversation-scoped queries (e.g.
    # the admin listing endpoint) don't need a join through messages.
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "conversations.id", ondelete="CASCADE", name="pitch_evaluations_conversation_id_fkey"
        ),
        nullable=False,
    )
    output_format: Mapped[str] = mapped_column(Text, nullable=False)
    # The full {"rules": [{"id", "status", "reason"}, ...]} array from
    # evaluate_pitch() -- kept as one JSONB blob rather than a child table
    # since it's always read/written whole, never queried rule-by-rule.
    rubric_results: Mapped[list] = mapped_column(JSONB, nullable=False)
    overall_score: Mapped[int] = mapped_column(Integer, nullable=False)
    top_gaps: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
