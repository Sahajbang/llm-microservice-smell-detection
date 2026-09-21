"""Step 2 tests.

Two layers, per CLAUDE.md's "how to test independently" guidance for Step 2:
  1. Unit tests against small synthetic module trees covering each detected
     pattern (Feign, RestTemplate, WebClient-via-indirection, DiscoveryClient,
     docker-compose depends_on).
  2. An integration test against the real Phase 1 target repo (target-repo/,
     spring-petclinic-microservices + the intentionally-introduced fixture
     documented in target-repo/FIXTURE_NOTES.md), asserting the extractor
     finds exactly the edge set that was manually verified by reading the
     source (see conversation/decision record) -- this doubles as a
     regression lock on that ground truth.
"""

from pathlib import Path

import pytest

from pipeline.extractor import discover_services, extract_all

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_REPO = REPO_ROOT / "target-repo"


def _write_module(root: Path, module_name: str, service_name: str, java_files: dict[str, str]) -> None:
    module_dir = root / module_name
    (module_dir).mkdir(parents=True, exist_ok=True)
    (module_dir / "pom.xml").write_text(
        f"<project><artifactId>{module_name}</artifactId></project>", encoding="utf-8"
    )
    resources = module_dir / "src" / "main" / "resources"
    resources.mkdir(parents=True, exist_ok=True)
    (resources / "application.yml").write_text(
        f"spring:\n  application:\n    name: {service_name}\n", encoding="utf-8"
    )
    java_root = module_dir / "src" / "main" / "java"
    for rel_path, content in java_files.items():
        f = java_root / rel_path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")


@pytest.fixture
def synthetic_repo(tmp_path: Path) -> Path:
    _write_module(
        tmp_path,
        "order-mod",
        "order-service",
        {
            "OrderClient.java": (
                '@FeignClient(name = "payment-service")\n'
                "public interface PaymentClient {}\n"
            )
        },
    )
    _write_module(
        tmp_path,
        "payment-mod",
        "payment-service",
        {
            "PaymentCaller.java": (
                "class PaymentCaller {\n"
                "  private final WebClient webClient;\n"
                '  private String hostname = "http://order-service/";\n'
                "  void call() {\n"
                '    webClient.get().uri(hostname + "orders").retrieve();\n'
                "  }\n"
                "}\n"
            )
        },
    )
    _write_module(
        tmp_path,
        "shipping-mod",
        "shipping-service",
        {
            "ShippingClient.java": (
                "class ShippingClient {\n"
                "  void call() {\n"
                '    restTemplate.getForObject("http://order-service/orders/{id}", Order.class, id);\n'
                "  }\n"
                "}\n"
            )
        },
    )
    _write_module(
        tmp_path,
        "billing-mod",
        "billing-service",
        {
            "BillingLookup.java": (
                "class BillingLookup {\n"
                "  void call() {\n"
                '    discoveryClient.getInstances("order-service");\n'
                "  }\n"
                "}\n"
            )
        },
    )
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  order-service:\n"
        "    depends_on:\n"
        "      config-server:\n"
        "        condition: service_healthy\n",
        encoding="utf-8",
    )
    return tmp_path


def test_discover_services_reads_application_name(synthetic_repo: Path):
    services = discover_services(synthetic_repo)
    assert services == {
        "order-mod": "order-service",
        "payment-mod": "payment-service",
        "shipping-mod": "shipping-service",
        "billing-mod": "billing-service",
    }


def _write_gradle_module(root: Path, module_name: str, *, application_name: str | None = None) -> None:
    module_dir = root / module_name
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "build.gradle").write_text("plugins { id 'org.springframework.boot' }\n", encoding="utf-8")
    if application_name:
        resources = module_dir / "src" / "main" / "resources"
        resources.mkdir(parents=True, exist_ok=True)
        (resources / "application.properties").write_text(
            f"spring.application.name={application_name}\n", encoding="utf-8"
        )


def test_discover_services_recognizes_gradle_modules(tmp_path: Path):
    """A repository with no pom.xml anywhere (e.g. microsoft/PartsUnlimitedMRPmicro)
    must not be treated as having zero modules just because it uses Gradle."""
    _write_gradle_module(tmp_path, "CatalogSrvc", application_name="catalog-catalogservice")
    services = discover_services(tmp_path)
    assert services == {"CatalogSrvc": "catalog-catalogservice"}


def test_discover_services_gradle_module_without_application_name_falls_back_to_dir_name(tmp_path: Path):
    """Gradle has no artifactId equivalent to fall back to; a standalone module
    (no settings.gradle) defaults to its own directory name under Gradle itself,
    so mirroring that is more honest than inventing a name or skipping it."""
    _write_gradle_module(tmp_path, "RestAPIGateway")
    assert discover_services(tmp_path) == {"RestAPIGateway": "RestAPIGateway"}


def test_discover_services_ignores_non_java_modules(tmp_path: Path):
    """A .csproj-only directory (e.g. PartsUnlimitedMRPmicro's DealerService) has
    no pom.xml or build.gradle and must stay invisible, not crash or get guessed."""
    module_dir = tmp_path / "DealerService"
    module_dir.mkdir(parents=True)
    (module_dir / "DealerService.csproj").write_text("<Project />", encoding="utf-8")
    assert discover_services(tmp_path) == {}


def test_extract_all_finds_every_pattern(synthetic_repo: Path):
    edges = extract_all(synthetic_repo)
    pairs = {(e["caller"], e["callee"], e["source"]) for e in edges}

    assert ("order-service", "payment-service", "feign") in pairs
    assert ("payment-service", "order-service", "webclient") in pairs
    assert ("shipping-service", "order-service", "resttemplate") in pairs
    assert ("billing-service", "order-service", "discovery-client") in pairs
    assert ("order-service", "config-server", "docker-compose") in pairs


def test_extract_all_ignores_self_references(tmp_path: Path):
    _write_module(
        tmp_path,
        "lonely-mod",
        "lonely-service",
        {
            "Self.java": (
                'class Self { String u = "http://lonely-service/health"; }\n'
            )
        },
    )
    edges = extract_all(tmp_path)
    assert edges == []


@pytest.mark.skipif(not TARGET_REPO.exists(), reason="target-repo/ not present")
def test_target_repo_ground_truth():
    """Locks in the manually-verified edge set for the Phase 1 target repo."""
    edges = extract_all(TARGET_REPO)
    pairs = {(e["caller"], e["callee"], e["source"]) for e in edges}

    expected = {
        ("api-gateway", "customers-service", "webclient"),
        ("api-gateway", "visits-service", "webclient"),
        ("genai-service", "vets-service", "webclient"),
        ("genai-service", "customers-service", "discovery-client"),
        ("customers-service", "visits-service", "resttemplate"),
        ("visits-service", "customers-service", "resttemplate"),
    }
    assert expected <= pairs

    # No other non-docker-compose edges should have been found -- this repo
    # was fully read by hand and these six are the whole real+fixture set.
    non_compose = {p for p in pairs if p[2] != "docker-compose"}
    assert non_compose == expected
