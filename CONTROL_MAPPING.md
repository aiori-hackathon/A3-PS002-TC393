# Selected DPDP Control Mapping for the Prototype

This is a working mapping for the hackathon prototype and mentor review. It is not a complete compliance checklist or legal opinion. Confirm the applicable provisions and commencement dates against the current official documents.

## Official documents used

- [Digital Personal Data Protection Act, 2023](https://www.indiacode.nic.in/bitstream/123456789/22037/1/a2023-22.pdf)
- [Digital Personal Data Protection Rules, 2025 and related documents](https://www.meity.gov.in/documents/act-and-policies/digital-personal-data-protection-rules-2025-gDOxUjMtQWa)
- [MeitY explanatory note to the DPDP Rules, 2025](https://www.meity.gov.in/writereaddata/files/Explanatory-Note-DPDP-Rules-2025.pdf)
- [DPDP Act commencement notification and timeline](https://www.meity.gov.in/static/uploads/2025/11/c56ceae6c383460ca69577428d36828b.pdf)

The Rules include a corrigendum and the Act has a phased commencement timeline. Use the notified Rules and current timeline when reviewing the mapping; the January 2025 draft Rules are not the final text.

## Control-to-evidence matrix

| Source | Selected control to demonstrate | Prototype behavior and evidence | Related lifecycle tests |
|---|---|---|---|
| DPDP Act, Section 5; DPDP Rules, Rule 3 | Give a clear notice that identifies the data and purpose. | Each purpose has a separately displayed notice with itemized synthetic data fields, a version, and a SHA-256 hash. `notice_versions` preserves prior notice text and hashes. | `test_15_consent_event_records_current_notice_hash`; `test_07_notice_change_requires_fresh_consent` |
| DPDP Act, Section 6(1) | Record a clear affirmative choice for the specified purpose. | Grant and refusal are separate actions for each purpose. A grant for one purpose does not authorize another. Consent rows include subject ID, purpose, action, UTC time, notice version, and notice hash. | `test_02_grant_allows_matching_purpose`; `test_03_grant_for_one_purpose_does_not_allow_another`; `test_04_refusal_denies_sharing` |
| DPDP Act, Section 6(4) | Make withdrawal available with ease comparable to giving consent. | The same purpose card exposes Grant and Withdraw controls. A withdrawal appends an event; it does not erase earlier events. | `test_05_withdrawal_denies_the_next_request`; `test_06_withdrawal_is_scoped_to_one_purpose` |
| DPDP Act, Section 6(6) | After consent withdrawal, cease consent-based processing within the period and subject to the conditions stated in the Act. | The mock API reads the current event on every request and denies the next request after withdrawal. This immediate API behavior is the hackathon demonstration requirement. | `test_05_withdrawal_denies_the_next_request` |
| DPDP Act, Section 8; DPDP Rules, Rule 6 | Demonstrate selected safeguards and visibility into access decisions. | SQLite queries use bound parameters. The API returns only synthetic fields in the selected purpose allow-list. Audit records capture allowed and denied requests and can be exported. This prototype does not implement all safeguards or the Rules' operational retention requirements. | `test_09_request_for_field_outside_purpose_scope_is_denied`; `test_13_audit_csv_contains_consent_and_data_request_events`; `test_14_audit_json_includes_denied_requests` |
| Hackathon expected outcome | Enforce consent through a mock data-sharing API and show lifecycle behavior. | `POST /api/v1/data/share` checks the current purpose-specific consent and current notice hash. Missing, refused, withdrawn, or stale grants return `DENY`; matching current grants return only requested synthetic fields. | `test_01_missing_consent_denies_sharing` through `test_14_audit_json_includes_denied_requests` |
| Hackathon expected outcome | Show a notice change and retain audit evidence. | The demo-only notice-change action creates a new version and hash. A previous grant becomes `RECONSENT_REQUIRED`; the notice update and later consent/API decisions appear in the audit export. | `test_07_notice_change_requires_fresh_consent`; `test_08_fresh_consent_for_new_notice_allows_sharing` |

## Important distinctions

- The Act says processing should cease within a reasonable time after withdrawal, subject to the Act and Rules. The hackathon asks the mock API to deny the next request immediately. The demo implements that immediate check as a clear technical control.
- The SHA-256 notice hash is an engineering traceability choice in this prototype. It binds a recorded consent to the exact notice text; the prototype does not represent hashing as a standalone statutory requirement.
- Audit export and at least 12 lifecycle tests are challenge deliverables. The prototype's 15 tests provide evidence for the selected scenarios, not proof of complete legal compliance.
- This app is a local synthetic workflow. It is not a registered Consent Manager, does not authenticate real users or services, and must not contain real personal data.
