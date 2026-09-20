/*
 * Copyright 2002-2021 the original author or authors.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package org.springframework.samples.petclinic.customers.web;

import java.util.List;
import java.util.Map;

import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

/**
 * Calls visits-service to fetch the visit history for a pet.
 *
 * NOTE: this class is part of an intentionally-introduced fixture for the
 * LLM-Guided Microservice Code Smell Detection thesis project (Phase 1).
 * It creates a genuine, statically-detectable Cyclic Dependency between
 * customers-service and visits-service (visits-service calls back into
 * customers-service via {@link CustomersServiceClient} in its own module).
 * See /FIXTURE_NOTES.md at the repository root for details and rationale.
 */
@Component
public class VisitsServiceClient {

    private final RestTemplate restTemplate;

    public VisitsServiceClient(RestTemplate loadBalancedRestTemplate) {
        this.restTemplate = loadBalancedRestTemplate;
    }

    /**
     * Returns how many visits are on record for the given pet, by calling
     * visits-service's {@code GET pets/visits?petId=} endpoint.
     */
    @SuppressWarnings("unchecked")
    public int getVisitCount(int petId) {
        Map<String, Object> response = restTemplate.getForObject(
            "http://visits-service/pets/visits?petId={petId}", Map.class, petId);
        if (response == null || response.get("items") == null) {
            return 0;
        }
        return ((List<Object>) response.get("items")).size();
    }
}
