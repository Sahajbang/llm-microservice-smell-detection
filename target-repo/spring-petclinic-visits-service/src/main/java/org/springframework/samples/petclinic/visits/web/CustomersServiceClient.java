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
package org.springframework.samples.petclinic.visits.web;

import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestTemplate;

/**
 * Calls customers-service to confirm a pet exists.
 *
 * NOTE: this class is part of an intentionally-introduced fixture for the
 * LLM-Guided Microservice Code Smell Detection thesis project (Phase 1).
 * It creates a genuine, statically-detectable Cyclic Dependency between
 * visits-service and customers-service (customers-service calls back into
 * visits-service via {@code VisitsServiceClient} in its own module).
 * See /FIXTURE_NOTES.md at the repository root for details and rationale.
 */
@Component
public class CustomersServiceClient {

    private final RestTemplate restTemplate;

    public CustomersServiceClient(RestTemplate loadBalancedRestTemplate) {
        this.restTemplate = loadBalancedRestTemplate;
    }

    /**
     * Returns whether the given pet is known to customers-service, by
     * calling its {@code GET owners/*&#47;pets/{petId}} endpoint.
     */
    public boolean petExists(int petId) {
        try {
            restTemplate.getForObject(
                "http://customers-service/owners/-/pets/{petId}", Object.class, petId);
            return true;
        } catch (HttpClientErrorException.NotFound e) {
            return false;
        }
    }
}
