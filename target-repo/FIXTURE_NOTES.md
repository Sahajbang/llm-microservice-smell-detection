# Fixture Notes — Intentionally Introduced Cyclic Dependency

This repository is [spring-petclinic-microservices](https://github.com/spring-petclinic/spring-petclinic-microservices),
cloned unmodified and verified to build and test clean (`mvn clean install`, `mvn test`,
JDK 17) before any changes were made.

In its original form this repo has **no cyclic dependency**: the only cross-service
calls are a clean fan-out from `api-gateway` and `genai-service` down to
`customers-service`, `visits-service`, and `vets-service`. That's a real limitation
for a project whose Phase 1 target smell is Cyclic Dependency — there was nothing to
detect or refactor.

To make the detection → refactoring → verification loop demonstrable end-to-end, a
**minimal, clearly-labeled synthetic cyclic dependency** was added between
`customers-service` and `visits-service`. This was a deliberate choice made with the
project author (see chat log / decision record), not an accidental side effect of
other work, and not a claim that the upstream project has this smell.

## What was added

- **`customers-service` → `visits-service`**: a new `VisitsServiceClient`
  (`org.springframework.samples.petclinic.customers.web.VisitsServiceClient`) calls
  visits-service's existing `GET pets/visits?petId=` endpoint, exposed through a new,
  additive `GET owners/*/pets/{petId}/visit-summary` endpoint
  (`PetVisitSummaryResource`).
- **`visits-service` → `customers-service`**: a new `CustomersServiceClient`
  (`org.springframework.samples.petclinic.visits.web.CustomersServiceClient`) calls
  customers-service's existing `GET owners/*/pets/{petId}` endpoint, exposed through a
  new, additive `GET pets/{petId}/exists-check` endpoint (`VisitValidationResource`).
- A `@LoadBalanced RestTemplate` bean (`RestTemplateConfig`) and the
  `spring-cloud-starter-loadbalancer` dependency were added to both services' poms to
  support the calls above.

Both additions are **new, self-contained endpoints** — neither the pre-existing
`PetResource`/`OwnerResource` (customers-service) nor `VisitResource` (visits-service)
nor their tests were modified. Unit tests were added for both new endpoints
(`PetVisitSummaryResourceTest`, `VisitValidationResourceTest`), following the existing
`@WebMvcTest` + `@MockitoBean` pattern already used in this codebase.

## Resulting dependency graph

```
api-gateway     -> customers-service   (real, WebClient)
api-gateway     -> visits-service      (real, WebClient)
genai-service   -> customers-service   (real, WebClient)
genai-service   -> vets-service        (real, WebClient)
customers-service -> visits-service    (SYNTHETIC, RestTemplate)
visits-service  -> customers-service   (SYNTHETIC, RestTemplate)
```

`customers-service <-> visits-service` is a genuine 2-node cyclic dependency,
statically detectable by a regex/grep-based extractor exactly as CLAUDE.md's Step 2
describes, and resolvable by a real refactor (e.g. removing one direction, or
extracting the shared concern into an event/async flow or a third service) that the
pipeline's Step 5 (refactoring agent) can propose and Step 6 can verify by re-running
the extractor + cycle detector and confirming `mvn test` still passes.

## Verification after introducing the fixture

`mvn clean install -DskipTests` and `mvn test` were re-run for the whole reactor after
these changes and passed clean (see the pipeline's run logs under `/logs` for the
Step 1 re-verification record).
