package org.springframework.samples.petclinic.customers.web;

import java.util.Optional;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.samples.petclinic.customers.model.Pet;
import org.springframework.samples.petclinic.customers.model.PetRepository;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.BDDMockito.given;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * NOTE: covers a fixture endpoint intentionally introduced for the
 * LLM-Guided Microservice Code Smell Detection thesis project (Phase 1).
 * See /FIXTURE_NOTES.md at the repository root.
 */
@WebMvcTest(PetVisitSummaryResource.class)
@ActiveProfiles("test")
class PetVisitSummaryResourceTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    PetRepository petRepository;

    @MockitoBean
    VisitsServiceClient visitsServiceClient;

    @Test
    void shouldGetVisitSummary() throws Exception {
        Pet pet = new Pet();
        pet.setId(2);
        pet.setName("Basil");

        given(petRepository.findById(2)).willReturn(Optional.of(pet));
        given(visitsServiceClient.getVisitCount(2)).willReturn(3);

        mvc.perform(get("/owners/1/pets/2/visit-summary"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.petId").value(2))
            .andExpect(jsonPath("$.petName").value("Basil"))
            .andExpect(jsonPath("$.visitCount").value(3));
    }

    @Test
    void shouldReturnNotFoundWhenPetDoesNotExist() throws Exception {
        given(petRepository.findById(99)).willReturn(Optional.empty());

        mvc.perform(get("/owners/1/pets/99/visit-summary"))
            .andExpect(status().isNotFound());
    }
}
