import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "company"
    __table_args__ = (
        CheckConstraint(
            "corporate_number IS NULL OR length(corporate_number) = 13",
            name="ck_company_corporate_number_length",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # 法人番号API連携後に埋める。NULL同士はPostgreSQLでは重複とみなさない
    corporate_number: Mapped[str | None] = mapped_column(String(13), nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    official_url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    hq_prefecture: Mapped[str] = mapped_column(String(10), nullable=False)
    hq_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    estimated_category: Mapped[str] = mapped_column(String(50), nullable=False)
    tech_stack: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    hiring_roles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    llm_confidence: Mapped[str] = mapped_column(String(10), nullable=False)
    release_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    fields: Mapped[list["CompanyField"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    metrics: Mapped["CompanyMetrics | None"] = relationship(
        back_populates="company", cascade="all, delete-orphan", uselist=False
    )


class CompanyField(Base):
    __tablename__ = "company_field"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # manual / llm_inferred / official_site / houjin_scraping / github_api / edinet_api
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    raw_value: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    company: Mapped["Company"] = relationship(back_populates="fields")


class CompanyMetrics(Base):
    """外部サイト（OpenWork / Green）から取得した企業の働き方・採用指標。"""

    __tablename__ = "company_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("company.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # OpenWork データ
    openwork_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    openwork_review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_overtime_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    paid_leave_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_annual_salary: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Green / 求人サイト データ
    new_grad_salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_grad_salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remote_work_policy: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_grad_headcount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_current_openings: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Green 由来: 企業基本情報
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    average_age: Mapped[float | None] = mapped_column(Float, nullable=True)
    capital_10k_yen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_listed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    founded_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # OpenWork サブスコア（0.0〜5.0）
    ow_score_treatment: Mapped[float | None] = mapped_column(Float, nullable=True)
    ow_score_morale: Mapped[float | None] = mapped_column(Float, nullable=True)
    ow_score_openness: Mapped[float | None] = mapped_column(Float, nullable=True)
    ow_score_growth: Mapped[float | None] = mapped_column(Float, nullable=True)

    # メタ
    openwork_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    green_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    company: Mapped["Company"] = relationship(back_populates="metrics")


class ProcessingLog(Base):
    __tablename__ = "processing_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # phase_1_generate / phase_2_verify / phase_3_enrich / phase_4_load
    phase: Mapped[str] = mapped_column(String(50), nullable=False)
    # success / failure / partial
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    target_company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
