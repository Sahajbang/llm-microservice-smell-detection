"""Persistence evidence extractor tests.

Covers JDBC URL normalization (the identity used to compare services), the
configuration/entity/schema scanners, docker-compose environment wiring,
and the false-positive guards the detector depends on.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.config import SharedPersistenceConfig
from pipeline.extractors.persistence import extract_persistence_evidence, parse_db_url
from tests.conftest import datasource_yml, entity_java, write_module

IN_MEMORY = SharedPersistenceConfig().in_memory_drivers


# -- URL parsing --------------------------------------------------------------


def test_parses_networked_mysql_url():
    parsed = parse_db_url("jdbc:mysql://mysql-server:3306/petclinic?useSSL=false", IN_MEMORY)

    assert parsed["driver"] == "mysql"
    assert parsed["host"] == "mysql-server"
    assert parsed["port"] == "3306"
    assert parsed["database"] == "petclinic"
    assert parsed["identity"] == "mysql://mysql-server:3306/petclinic"
    assert parsed["in_memory"] is False
    assert parsed["resolved"] is True


def test_parses_postgres_and_r2dbc_urls():
    assert parse_db_url("jdbc:postgresql://db:5432/orders", IN_MEMORY)["database"] == "orders"
    assert parse_db_url("r2dbc:postgresql://db:5432/orders", IN_MEMORY)["driver"] == "postgresql"


def test_parses_sqlserver_database_parameter_form():
    parsed = parse_db_url("jdbc:sqlserver://host:1433;databaseName=sales", IN_MEMORY)
    assert parsed["database"] == "sales"


def test_marks_in_memory_databases():
    for url in ("jdbc:hsqldb:mem:petclinic", "jdbc:h2:mem:testdb;DB_CLOSE_DELAY=-1"):
        parsed = parse_db_url(url, IN_MEMORY)
        assert parsed["in_memory"] is True, url


def test_file_based_h2_is_not_in_memory():
    assert parse_db_url("jdbc:h2:file:./data/orders", IN_MEMORY)["in_memory"] is False


def test_unresolved_placeholders_are_flagged():
    parsed = parse_db_url("jdbc:mysql://${DB_HOST}:3306/shop", IN_MEMORY)
    assert parsed["resolved"] is False


def test_identical_urls_produce_identical_identities():
    a = parse_db_url("jdbc:mysql://DB:3306/Shop", IN_MEMORY)["identity"]
    b = parse_db_url("jdbc:mysql://db:3306/shop", IN_MEMORY)["identity"]
    assert a == b, "identity comparison must be case-insensitive"


# -- Configuration scanning ---------------------------------------------------


def test_extracts_datasource_from_application_yml(tmp_path: Path):
    write_module(
        tmp_path,
        "orders-mod",
        "orders-service",
        application_yml=datasource_yml("orders-service", "jdbc:mysql://db:3306/shop"),
    )
    records = extract_persistence_evidence(tmp_path, {"orders-mod": "orders-service"})
    datasources = [r for r in records if r.kind == "datasource"]

    assert len(datasources) == 1
    assert datasources[0].service == "orders-service"
    assert datasources[0].value == "mysql://db:3306/shop"
    assert datasources[0].line > 0
    assert datasources[0].confidence == SharedPersistenceConfig().confidence_resolved_datasource


def test_extracts_datasource_from_properties_file(tmp_path: Path):
    module = write_module(tmp_path, "orders-mod", "orders-service")
    (module / "src" / "main" / "resources" / "application-prod.properties").write_text(
        "spring.datasource.url=jdbc:postgresql://pg:5432/shop\n", encoding="utf-8"
    )
    records = extract_persistence_evidence(tmp_path, {"orders-mod": "orders-service"})
    assert any(r.value == "postgresql://pg:5432/shop" for r in records)


def test_unresolved_datasource_gets_lower_confidence(tmp_path: Path):
    write_module(
        tmp_path,
        "orders-mod",
        "orders-service",
        application_yml=datasource_yml("orders-service", "jdbc:mysql://${DB_HOST}:3306/shop"),
    )
    record = [r for r in extract_persistence_evidence(tmp_path, {"orders-mod": "orders-service"}) if r.kind == "datasource"][0]

    assert record.metadata["resolved"] is False
    assert record.confidence == SharedPersistenceConfig().confidence_unresolved_datasource


def test_explicit_schema_setting_is_captured(tmp_path: Path):
    write_module(
        tmp_path,
        "orders-mod",
        "orders-service",
        application_yml=(
            "spring:\n"
            "  application:\n"
            "    name: orders-service\n"
            "  jpa:\n"
            "    properties:\n"
            "      hibernate:\n"
            "        default_schema: shared_core\n"
        ),
    )
    records = extract_persistence_evidence(tmp_path, {"orders-mod": "orders-service"})
    assert any(r.value == "schema:shared_core" for r in records)


def test_test_resources_are_ignored(tmp_path: Path):
    """A shared H2 in tests must never become production evidence."""
    module = write_module(tmp_path, "orders-mod", "orders-service")
    test_resources = module / "src" / "test" / "resources"
    test_resources.mkdir(parents=True)
    (test_resources / "application.yml").write_text(
        "spring:\n  datasource:\n    url: jdbc:mysql://shared:3306/everything\n", encoding="utf-8"
    )
    records = extract_persistence_evidence(tmp_path, {"orders-mod": "orders-service"})
    assert not any("everything" in r.value for r in records)


# -- Entities and schemas -----------------------------------------------------


def test_extracts_explicit_jpa_table(tmp_path: Path):
    write_module(
        tmp_path,
        "orders-mod",
        "orders-service",
        java_files={"Order.java": entity_java("Order", "orders")},
    )
    records = extract_persistence_evidence(tmp_path, {"orders-mod": "orders-service"})
    entities = [r for r in records if r.kind == "entity"]

    assert [e.value for e in entities] == ["orders"]
    assert entities[0].metadata["explicit"] is True


def test_entity_without_table_annotation_is_low_confidence_and_inferred(tmp_path: Path):
    write_module(
        tmp_path,
        "orders-mod",
        "orders-service",
        java_files={"Order.java": entity_java("Order")},
    )
    entity = [r for r in extract_persistence_evidence(tmp_path, {"orders-mod": "orders-service"}) if r.kind == "entity"][0]

    assert entity.metadata["explicit"] is False
    assert entity.metadata["inferred_from_class_name"] is True
    assert entity.confidence < 0.5


def test_non_entity_java_file_is_ignored(tmp_path: Path):
    write_module(
        tmp_path,
        "orders-mod",
        "orders-service",
        java_files={"Helper.java": "class Helper { }\n"},
    )
    records = extract_persistence_evidence(tmp_path, {"orders-mod": "orders-service"})
    assert [r for r in records if r.kind == "entity"] == []


def test_extracts_create_table_from_schema_sql(tmp_path: Path):
    write_module(
        tmp_path,
        "orders-mod",
        "orders-service",
        resources={
            "db/mysql/schema.sql": (
                "CREATE TABLE IF NOT EXISTS orders (id INT);\n"
                "CREATE TABLE order_lines (id INT);\n"
            )
        },
    )
    tables = [r for r in extract_persistence_evidence(tmp_path, {"orders-mod": "orders-service"}) if r.kind == "table"]
    assert {t.value for t in tables} == {"orders", "order_lines"}


def test_extracts_flyway_migration_tables(tmp_path: Path):
    write_module(
        tmp_path,
        "orders-mod",
        "orders-service",
        resources={"db/migration/V1__init.sql": "CREATE TABLE shipments (id INT);\n"},
    )
    tables = [r for r in extract_persistence_evidence(tmp_path, {"orders-mod": "orders-service"}) if r.kind == "table"]
    assert tables[0].value == "shipments"
    assert tables[0].metadata["migration"] is True


def test_extracts_liquibase_create_table(tmp_path: Path):
    write_module(
        tmp_path,
        "orders-mod",
        "orders-service",
        resources={
            "db/changelog/changes.xml": (
                '<databaseChangeLog><changeSet><createTable tableName="invoices"/></changeSet></databaseChangeLog>'
            )
        },
    )
    tables = [r for r in extract_persistence_evidence(tmp_path, {"orders-mod": "orders-service"}) if r.kind == "table"]
    assert [t.value for t in tables] == ["invoices"]


# -- docker-compose wiring ----------------------------------------------------


def test_extracts_datasource_from_compose_environment(tmp_path: Path):
    write_module(tmp_path, "orders-mod", "orders-service")
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  orders-service:\n"
        "    environment:\n"
        "      - SPRING_DATASOURCE_URL=jdbc:mysql://db:3306/shared\n"
        "  billing-service:\n"
        "    environment:\n"
        "      SPRING_DATASOURCE_URL: jdbc:mysql://db:3306/shared\n",
        encoding="utf-8",
    )
    records = extract_persistence_evidence(tmp_path, {"orders-mod": "orders-service"})
    from_compose = [r for r in records if r.metadata.get("from") == "docker-compose-environment"]

    assert {r.service for r in from_compose} == {"orders-service", "billing-service"}
    assert {r.value for r in from_compose} == {"mysql://db:3306/shared"}


def test_malformed_compose_file_does_not_raise(tmp_path: Path):
    write_module(tmp_path, "orders-mod", "orders-service")
    (tmp_path / "docker-compose.yml").write_text("services: [unclosed\n", encoding="utf-8")
    assert extract_persistence_evidence(tmp_path, {"orders-mod": "orders-service"}) == []


def test_repository_without_persistence_yields_nothing(tmp_path: Path):
    """Absence of evidence is reported as absence, not as an error."""
    write_module(tmp_path, "gateway-mod", "gateway-service")
    assert extract_persistence_evidence(tmp_path, {"gateway-mod": "gateway-service"}) == []
