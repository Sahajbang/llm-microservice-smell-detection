"""
Shared fixtures for the pipeline test suite.

Builds small synthetic Spring-Boot-shaped repositories on disk so that
extraction, detection and the end-to-end orchestrator can be exercised
without cloning anything or calling any API. Nothing here touches the
network or a real LLM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest


def write_module(
    root: Path,
    module_name: str,
    service_name: str,
    *,
    java_files: Optional[dict[str, str]] = None,
    resources: Optional[dict[str, str]] = None,
    application_yml: Optional[str] = None,
) -> Path:
    """Create one Maven module directory with the shape the extractors expect."""
    module_dir = root / module_name
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "pom.xml").write_text(
        f"<project><artifactId>{module_name}</artifactId></project>", encoding="utf-8"
    )

    resources_dir = module_dir / "src" / "main" / "resources"
    resources_dir.mkdir(parents=True, exist_ok=True)
    (resources_dir / "application.yml").write_text(
        application_yml
        if application_yml is not None
        else f"spring:\n  application:\n    name: {service_name}\n",
        encoding="utf-8",
    )

    for rel_path, content in (resources or {}).items():
        target = resources_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    java_root = module_dir / "src" / "main" / "java"
    for rel_path, content in (java_files or {}).items():
        target = java_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    return module_dir


def datasource_yml(service_name: str, jdbc_url: str) -> str:
    return (
        "spring:\n"
        "  application:\n"
        f"    name: {service_name}\n"
        "  datasource:\n"
        f"    url: {jdbc_url}\n"
        "    username: app\n"
    )


def entity_java(class_name: str, table_name: Optional[str] = None) -> str:
    table_annotation = f'@Table(name = "{table_name}")\n' if table_name else ""
    return (
        "import jakarta.persistence.*;\n\n"
        "@Entity\n"
        f"{table_annotation}"
        f"public class {class_name} {{\n"
        "    @Id private Long id;\n"
        "}\n"
    )


@pytest.fixture
def cycle_repo(tmp_path: Path) -> Path:
    """Two services calling each other: the minimal Cyclic Dependency case."""
    write_module(
        tmp_path,
        "order-mod",
        "order-service",
        java_files={
            "PaymentClient.java": (
                '@FeignClient(name = "payment-service")\npublic interface PaymentClient {}\n'
            )
        },
    )
    write_module(
        tmp_path,
        "payment-mod",
        "payment-service",
        java_files={
            "OrderClient.java": (
                '@FeignClient(name = "order-service")\npublic interface OrderClient {}\n'
            )
        },
    )
    return tmp_path


@pytest.fixture
def multi_smell_repo(tmp_path: Path) -> Path:
    """A repository containing all three smells at once.

    * Cyclic Dependency: order-service <-> payment-service.
    * Hub-like Dependency: order-service touches all three other services.
    * Shared Persistence: order-service and billing-service share one MySQL
      database and both declare an ``orders`` table.
    """
    write_module(
        tmp_path,
        "order-mod",
        "order-service",
        application_yml=datasource_yml("order-service", "jdbc:mysql://db:3306/shopdb"),
        java_files={
            "PaymentClient.java": '@FeignClient(name = "payment-service")\ninterface PaymentClient {}\n',
            "ShippingCaller.java": (
                "class ShippingCaller {\n"
                "  void call() {\n"
                '    restTemplate.getForObject("http://shipping-service/ship/{id}", Ship.class, id);\n'
                "  }\n"
                "}\n"
            ),
            "BillingCaller.java": (
                "class BillingCaller {\n"
                "  void call() {\n"
                '    restTemplate.getForObject("http://billing-service/invoices", Inv.class);\n'
                "  }\n"
                "}\n"
            ),
            "Order.java": entity_java("Order", "orders"),
        },
    )
    write_module(
        tmp_path,
        "payment-mod",
        "payment-service",
        java_files={
            "OrderClient.java": '@FeignClient(name = "order-service")\ninterface OrderClient {}\n'
        },
    )
    write_module(tmp_path, "shipping-mod", "shipping-service")
    write_module(
        tmp_path,
        "billing-mod",
        "billing-service",
        application_yml=datasource_yml("billing-service", "jdbc:mysql://db:3306/shopdb"),
        java_files={"OrderRow.java": entity_java("OrderRow", "orders")},
    )
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  order-service:\n"
        "    depends_on:\n"
        "      - db\n"
        "  db:\n"
        "    image: mysql:8\n",
        encoding="utf-8",
    )
    return tmp_path
