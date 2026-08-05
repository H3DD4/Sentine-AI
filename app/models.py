from sqlalchemy import Column, String, Float, DateTime, Text, JSON, Enum as SAEnum, ForeignKey, Integer, Boolean
from sqlalchemy.orm import DeclarativeBase, relationship
from datetime import datetime
import enum, uuid

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class VerdictEnum(str, enum.Enum):
    confirmed = "confirmed"
    likely = "likely"
    insufficient = "insufficient"
    false_positive = "false_positive"


# NOTE: the former single `KBEntry` / `kb_entries` table has been replaced by
# one table per knowledge source — see app/kb/models.py. Mixing NVD CVEs, MITRE
# techniques and Ghostwriter findings in one table forced a lowest-common-
# denominator schema and made per-source availability impossible to report.

class Finding(Base):
    __tablename__ = "findings"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String)
    description = Column(Text)
    verdict = Column(SAEnum(VerdictEnum), nullable=True)
    confidence = Column(Float, nullable=True)
    reasoning = Column(Text, nullable=True)
    matched_cves = Column(JSON, default=list)
    matched_techniques = Column(JSON, default=list)
    missing_evidence = Column(JSON, default=list)
    recommended_next_steps = Column(JSON, default=list)
    analyst_confirmed = Column(Boolean, default=False)
    ghostwriter_finding_id = Column(String, nullable=True)
    # Findings were previously orphaned from engagements, so reports could not
    # be scoped to a client and the Engagement.findings_count was unmaintainable.
    engagement_id = Column(String, ForeignKey("engagements.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    evidence = relationship("Evidence", back_populates="finding")
    engagement = relationship("Engagement", back_populates="findings")

class Evidence(Base):
    __tablename__ = "evidence"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    finding_id = Column(String, ForeignKey("findings.id"))
    filename = Column(String)
    file_type = Column(String)    # "image" | "pdf" | "text" | "log" | "binary"
    storage_path = Column(String)
    extracted_text = Column(Text, nullable=True)
    image_description = Column(Text, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    finding = relationship("Finding", back_populates="evidence")

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type = Column(String)     # "validation" | "report_generated" | "ghostwriter_push" | "kb_add"
    finding_id = Column(String, nullable=True)
    user_id = Column(String, nullable=True)
    input_hash = Column(String)
    payload_summary = Column(JSON)
    result_summary = Column(JSON)
    timestamp = Column(DateTime, default=datetime.utcnow)

class Engagement(Base):
    __tablename__ = "engagements"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    client_name = Column(String)
    code = Column(String, unique=True)
    scope = Column(String)
    progress = Column(Float, default=0.0)
    findings_count = Column(Integer, default=0)
    lead = Column(String)
    status = Column(String, default="active")  # "active" | "reporting" | "scoping"
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    findings = relationship("Finding", back_populates="engagement")