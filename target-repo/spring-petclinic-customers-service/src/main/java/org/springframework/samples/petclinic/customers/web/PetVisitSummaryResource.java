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

import jakarta.validation.constraints.Min;

import org.springframework.samples.petclinic.customers.model.Pet;
import org.springframework.samples.petclinic.customers.model.PetRepository;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

/**
 * Exposes a pet's visit summary by combining local pet data with a call out
 * to visits-service.
 *
 * NOTE: this controller is part of an intentionally-introduced fixture for
 * the LLM-Guided Microservice Code Smell Detection thesis project (Phase 1).
 * It is additive only (a brand new endpoint) so it does not change the
 * behaviour or tests of the pre-existing {@link PetResource}. See
 * /FIXTURE_NOTES.md at the repository root for details and rationale.
 */
@RestController
class PetVisitSummaryResource {

    private final PetRepository petRepository;
    private final VisitsServiceClient visitsServiceClient;

    PetVisitSummaryResource(PetRepository petRepository, VisitsServiceClient visitsServiceClient) {
        this.petRepository = petRepository;
        this.visitsServiceClient = visitsServiceClient;
    }

    @GetMapping("owners/*/pets/{petId}/visit-summary")
    public PetVisitSummary getVisitSummary(@PathVariable("petId") @Min(1) int petId) {
        Pet pet = petRepository.findById(petId)
            .orElseThrow(() -> new ResourceNotFoundException("Pet " + petId + " not found"));
        int visitCount = visitsServiceClient.getVisitCount(petId);
        return new PetVisitSummary(pet.getId(), pet.getName(), visitCount);
    }

    record PetVisitSummary(long petId, String petName, int visitCount) {
    }
}
