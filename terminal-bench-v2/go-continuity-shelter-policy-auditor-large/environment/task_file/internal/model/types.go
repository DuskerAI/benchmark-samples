package model

type Config struct {
    EvaluationDate string `json:"evaluation_date"`
    SchemaVersion  int    `json:"schema_version"`
}

type ExceptionOffer struct {
    Code      string `json:"code"`
    Source    string `json:"source"`
    ExpiresOn string `json:"expires_on"`
}

type Case struct {
    CaseID                  string           `json:"case_id"`
    Jurisdiction            string           `json:"jurisdiction"`
    OpenedAt                string           `json:"opened_at"`
    ShelterType             string           `json:"shelter_type"`
    Residents               int              `json:"residents"`
    StaffCount              int              `json:"staff_count"`
    MedicalDependent        bool             `json:"medical_dependent"`
    PetCount                int              `json:"pet_count"`
    PetAccommodation        bool             `json:"pet_accommodation"`
    EvacuationStatus        string           `json:"evacuation_status"`
    GeneratorClass          string           `json:"generator_class"`
    GeneratorTestedAt       string           `json:"generator_tested_at"`
    TransportStatus         string           `json:"transport_status"`
    WaterDays               int              `json:"water_days"`
    AccessibilityRetrofit   bool             `json:"accessibility_retrofit"`
    Exceptions              []ExceptionOffer `json:"exceptions"`
}

type EffectiveRule struct {
    ID       string `json:"id"`
    Value    string `json:"value"`
    Source   string `json:"source"`
    Priority int    `json:"priority"`
}

type EffectiveRules struct {
    SchemaVersion   int             `json:"schema_version"`
    PolicyDocuments int             `json:"policy_documents"`
    Rules           []EffectiveRule `json:"rules"`
}

type Decision struct {
    CaseID       string   `json:"case_id"`
    Jurisdiction string   `json:"jurisdiction"`
    Decision     string   `json:"decision"`
    Reasons      []string `json:"reasons"`
    AppliedRules []string `json:"applied_rules"`
}

type DecisionReport struct {
    SchemaVersion int        `json:"schema_version"`
    CaseCount     int        `json:"case_count"`
    Decisions     []Decision `json:"decisions"`
}

type ExceptionRow struct {
    CaseID        string
    ExceptionCode string
    Source        string
    ExpiresOn     string
    Accepted      bool
}

type Result struct {
    EffectiveRules EffectiveRules
    DecisionReport DecisionReport
    Exceptions     []ExceptionRow
}
