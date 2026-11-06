"""Data contracts: the schema and rules each table must satisfy to enter silver.

A contract is the explicit agreement between the source and our pipeline. Rows that
break it are not silently dropped: they go to `quarantine_<table>` with the reasons.
Types, required columns, and foreign keys come from the data dictionary PDF; accepted
enum values come from **profiling the real data** (the dictionary lists English values,
the files contain Spanish ones for several columns).
"""

from dataclasses import dataclass, field

MONEY = "DECIMAL(15,2)"


def _schema(spec: str) -> dict[str, str]:
    """'col TYPE' per line -> ordered {col: type}."""
    pairs = (line.split(maxsplit=1) for line in spec.strip().splitlines())
    return {name: typ.strip() for name, typ in pairs}


@dataclass(frozen=True)
class TableContract:
    name: str
    columns: dict[str, str]  # column -> DuckDB type
    primary_key: tuple[str, ...]
    required: frozenset[str]
    partitioned: bool  # daily hive partitions (facts) vs a single snapshot file
    version_column: str  # on duplicate keys, the highest version wins
    enums: dict[str, frozenset[str]] = field(default_factory=dict)
    foreign_keys: dict[str, str] = field(default_factory=dict)  # column -> parent table
    normalize: dict[str, dict[str, str]] = field(default_factory=dict)
    event_column: str | None = None  # business timestamp (freshness, late arrivals)
    expected_rows: int | None = None  # per the data dictionary, to report gaps


CONTRACTS: dict[str, TableContract] = {
    c.name: c
    for c in [
        TableContract(
            name="customers",
            columns=_schema(f"""
                customer_id VARCHAR
                document_number VARCHAR
                document_type VARCHAR
                first_name VARCHAR
                last_name VARCHAR
                date_of_birth DATE
                gender VARCHAR
                email VARCHAR
                mobile_phone VARCHAR
                landline_phone VARCHAR
                address VARCHAR
                city VARCHAR
                state VARCHAR
                country VARCHAR
                postal_code VARCHAR
                detected_accent VARCHAR
                segment VARCHAR
                credit_score INTEGER
                estimated_monthly_income {MONEY}
                occupation VARCHAR
                marital_status VARCHAR
                education_level VARCHAR
                registration_date TIMESTAMP
                registration_branch_id VARCHAR
                customer_status VARCHAR
                last_updated TIMESTAMP
                accepts_marketing BOOLEAN
            """),
            primary_key=("customer_id",),
            required=frozenset(
                {
                    "customer_id",
                    "document_number",
                    "document_type",
                    "first_name",
                    "last_name",
                    "date_of_birth",
                    "city",
                    "state",
                    "country",
                    "segment",
                    "registration_date",
                    "registration_branch_id",
                    "customer_status",
                    "last_updated",
                    "accepts_marketing",
                }
            ),
            partitioned=False,
            version_column="last_updated",
            enums={
                "document_type": frozenset({"DNI", "CURP", "CC", "CE", "Pasaporte"}),
                "country": frozenset({"México", "Colombia", "Argentina"}),
                "segment": frozenset({"Premium", "Plus", "Basic", "Student"}),
                "customer_status": frozenset({"Active", "Inactive", "Suspended", "Closed"}),
            },
            normalize={"country": {"Mexico": "México"}},
            expected_rows=150_000,
        ),
        TableContract(
            name="products",
            columns=_schema(f"""
                product_id VARCHAR
                customer_id VARCHAR
                product_type VARCHAR
                product_number VARCHAR
                currency VARCHAR
                current_balance {MONEY}
                credit_limit {MONEY}
                interest_rate DECIMAL(5,2)
                opening_date DATE
                expiration_date DATE
                opening_branch_id VARCHAR
                product_status VARCHAR
                opening_channel VARCHAR
                has_linked_app BOOLEAN
                days_past_due INTEGER
                last_transaction_date TIMESTAMP
                last_updated TIMESTAMP
            """),
            primary_key=("product_id",),
            required=frozenset(
                {
                    "product_id",
                    "customer_id",
                    "product_type",
                    "product_number",
                    "currency",
                    "current_balance",
                    "opening_date",
                    "opening_branch_id",
                    "product_status",
                    "opening_channel",
                    "has_linked_app",
                    "last_updated",
                }
            ),
            partitioned=False,
            version_column="last_updated",
            enums={
                "product_type": frozenset(
                    {
                        "Cuenta Ahorro",
                        "Cuenta Corriente",
                        "Tarjeta Crédito",
                        "Tarjeta Débito",
                        "Préstamo Personal",
                        "Préstamo Hipotecario",
                        "Inversión",
                        "Seguro",
                    }
                ),
                "currency": frozenset({"MXN", "COP", "ARS", "USD"}),
                "product_status": frozenset({"Active", "Blocked", "Closed", "Suspended"}),
            },
            foreign_keys={"customer_id": "customers"},
            expected_rows=400_000,
        ),
        TableContract(
            name="transactions",
            columns=_schema(f"""
                transaction_id VARCHAR
                transaction_date TIMESTAMP
                process_date DATE
                product_id VARCHAR
                customer_id VARCHAR
                transaction_type VARCHAR
                transaction_category VARCHAR
                amount {MONEY}
                currency VARCHAR
                amount_usd {MONEY}
                channel VARCHAR
                branch_id VARCHAR
                merchant_name VARCHAR
                merchant_category VARCHAR
                transaction_country VARCHAR
                transaction_city VARCHAR
                transaction_status VARCHAR
                response_code VARCHAR
                is_fraud BOOLEAN
                fraud_score DOUBLE
                latitude DOUBLE
                longitude DOUBLE
            """),
            primary_key=("transaction_id",),
            required=frozenset(
                {
                    "transaction_id",
                    "transaction_date",
                    "process_date",
                    "product_id",
                    "customer_id",
                    "transaction_type",
                    "amount",
                    "currency",
                    "channel",
                    "transaction_country",
                    "transaction_status",
                    "is_fraud",
                }
            ),
            partitioned=True,
            version_column="process_date",
            enums={
                "transaction_type": frozenset(
                    {"Deposit", "Withdrawal", "Transfer", "Payment", "Purchase", "Adjustment"}
                ),
                "currency": frozenset({"MXN", "COP", "ARS", "USD"}),
                "channel": frozenset({"ATM", "Branch", "Web", "App", "POS", "Transfer"}),
                "transaction_status": frozenset({"Approved", "Declined", "Pending", "Reversed"}),
            },
            foreign_keys={"product_id": "products", "customer_id": "customers"},
            normalize={"transaction_country": {"Mexico": "México"}},
            event_column="transaction_date",
            expected_rows=5_000_000,
        ),
        TableContract(
            name="call_center_interactions",
            columns=_schema("""
                interaction_id VARCHAR
                interaction_date TIMESTAMP
                process_date DATE
                customer_id VARCHAR
                agent_id VARCHAR
                interaction_type VARCHAR
                channel VARCHAR
                contact_reason VARCHAR
                reason_category VARCHAR
                duration_seconds INTEGER
                wait_time_seconds INTEGER
                was_resolved BOOLEAN
                requires_followup BOOLEAN
                detected_sentiment VARCHAR
                sentiment_score DOUBLE
                customer_detected_accent VARCHAR
                agent_used_accent VARCHAR
                was_escalated BOOLEAN
                mentioned_products VARCHAR
                has_transcript BOOLEAN
                has_recording BOOLEAN
            """),
            primary_key=("interaction_id",),
            required=frozenset(
                {
                    "interaction_id",
                    "interaction_date",
                    "process_date",
                    "customer_id",
                    "interaction_type",
                    "channel",
                    "contact_reason",
                    "reason_category",
                    "requires_followup",
                    "was_escalated",
                    "has_transcript",
                    "has_recording",
                }
            ),
            partitioned=True,
            version_column="process_date",
            enums={
                "reason_category": frozenset(
                    {"Transaccional", "Producto", "Queja", "Técnico", "Comercial", "Retención"}
                ),
                "detected_sentiment": frozenset(
                    {"Muy Positivo", "Positivo", "Neutral", "Negativo", "Muy Negativo"}
                ),
            },
            foreign_keys={"customer_id": "customers"},
            event_column="interaction_date",
            expected_rows=800_000,
        ),
        TableContract(
            name="call_transcripts",
            columns=_schema("""
                transcript_id VARCHAR
                interaction_id VARCHAR
                process_date DATE
                customer_id VARCHAR
                agent_id VARCHAR
                full_text VARCHAR
                customer_text VARCHAR
                agent_text VARCHAR
                detected_language VARCHAR
                detected_accent VARCHAR
                accent_confidence DOUBLE
                detected_keywords VARCHAR
                mentioned_entities VARCHAR
                detected_intents VARCHAR
                main_topics VARCHAR
                transcription_model VARCHAR
                audio_quality VARCHAR
                duration_seconds INTEGER
            """),
            primary_key=("transcript_id",),
            required=frozenset(
                {
                    "transcript_id",
                    "interaction_id",
                    "process_date",
                    "customer_id",
                    "agent_id",
                    "full_text",
                    "detected_language",
                    "transcription_model",
                    "duration_seconds",
                }
            ),
            partitioned=True,
            version_column="process_date",
            foreign_keys={"interaction_id": "call_center_interactions", "customer_id": "customers"},
            expected_rows=200_000,
        ),
        TableContract(
            name="satisfaction_surveys",
            columns=_schema("""
                survey_id VARCHAR
                survey_date TIMESTAMP
                process_date DATE
                interaction_id VARCHAR
                customer_id VARCHAR
                agent_id VARCHAR
                survey_type VARCHAR
                send_channel VARCHAR
                main_score INTEGER
                nps_category VARCHAR
                question_1_text VARCHAR
                question_1_response INTEGER
                question_2_text VARCHAR
                question_2_response INTEGER
                question_3_text VARCHAR
                question_3_response INTEGER
                open_comments VARCHAR
                comment_sentiment VARCHAR
                response_time_hours DOUBLE
                campaign_response_rate DOUBLE
            """),
            primary_key=("survey_id",),
            required=frozenset(
                {
                    "survey_id",
                    "survey_date",
                    "process_date",
                    "customer_id",
                    "survey_type",
                    "send_channel",
                    "main_score",
                }
            ),
            partitioned=True,
            version_column="process_date",
            enums={"survey_type": frozenset({"CSAT", "NPS", "CES"})},
            foreign_keys={"interaction_id": "call_center_interactions", "customer_id": "customers"},
            event_column="survey_date",
            expected_rows=250_000,
        ),
        TableContract(
            name="complaints",
            columns=_schema(f"""
                complaint_id VARCHAR
                creation_date TIMESTAMP
                process_date DATE
                customer_id VARCHAR
                case_type VARCHAR
                category VARCHAR
                subcategory VARCHAR
                reception_channel VARCHAR
                affected_product_id VARCHAR
                related_branch_id VARCHAR
                origin_interaction_id VARCHAR
                description VARCHAR
                claimed_amount {MONEY}
                currency VARCHAR
                priority VARCHAR
                status VARCHAR
                assigned_agent_id VARCHAR
                assignment_date TIMESTAMP
                first_response_date TIMESTAMP
                resolution_date TIMESTAMP
                closing_date TIMESTAMP
                sla_breached BOOLEAN
                resolution_days INTEGER
                resolution VARCHAR
                compensation_granted {MONEY}
                resolution_satisfaction INTEGER
                is_repeat_complainer BOOLEAN
            """),
            primary_key=("complaint_id",),
            required=frozenset(
                {
                    "complaint_id",
                    "creation_date",
                    "process_date",
                    "customer_id",
                    "case_type",
                    "category",
                    "reception_channel",
                    "description",
                    "priority",
                    "status",
                    "sla_breached",
                    "is_repeat_complainer",
                }
            ),
            partitioned=True,
            version_column="process_date",
            enums={
                "case_type": frozenset({"Complaint", "Claim", "Request", "Suggestion"}),
                "priority": frozenset({"Low", "Medium", "High", "Critical"}),
                "status": frozenset(
                    {"Open", "In Process", "Escalated", "Resolved", "Closed", "Rejected"}
                ),
            },
            foreign_keys={
                "customer_id": "customers",
                "affected_product_id": "products",
                "origin_interaction_id": "call_center_interactions",
            },
            event_column="creation_date",
            expected_rows=80_000,
        ),
        TableContract(
            name="daily_exchange_rates",
            columns=_schema("""
                date DATE
                source_currency VARCHAR
                target_currency VARCHAR
                exchange_rate DOUBLE
                buy_rate DOUBLE
                sell_rate DOUBLE
                source VARCHAR
            """),
            primary_key=("date", "source_currency", "target_currency"),
            required=frozenset({"date", "source_currency", "target_currency", "exchange_rate"}),
            partitioned=False,
            version_column="date",
            enums={
                "source_currency": frozenset({"MXN", "COP", "ARS", "USD"}),
                "target_currency": frozenset({"MXN", "COP", "ARS", "USD"}),
            },
            expected_rows=3_000,
        ),
    ]
}

# Load order: parents before children, so orphan checks see the latest parents.
LOAD_ORDER = (
    "customers",
    "products",
    "daily_exchange_rates",
    "call_center_interactions",
    "call_transcripts",
    "satisfaction_surveys",
    "complaints",
    "transactions",
)
