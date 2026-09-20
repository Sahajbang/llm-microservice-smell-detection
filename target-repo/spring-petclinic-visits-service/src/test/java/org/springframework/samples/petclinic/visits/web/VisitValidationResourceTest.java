package org.springframework.samples.petclinic.visits.web;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
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
@WebMvcTest(VisitValidationResource.class)
@ActiveProfiles("test")
class VisitValidationResourceTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    CustomersServiceClient customersServiceClient;

    @Test
    void shouldReportPetExists() throws Exception {
        given(customersServiceClient.petExists(111)).willReturn(true);

        mvc.perform(get("/pets/111/exists-check"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.petId").value(111))
            .andExpect(jsonPath("$.exists").value(true));
    }

    @Test
    void shouldReportPetDoesNotExist() throws Exception {
        given(customersServiceClient.petExists(999)).willReturn(false);

        mvc.perform(get("/pets/999/exists-check"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.petId").value(999))
            .andExpect(jsonPath("$.exists").value(false));
    }
}
