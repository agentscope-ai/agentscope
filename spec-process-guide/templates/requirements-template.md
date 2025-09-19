# Requirements Template

<!-- Navigation Metadata -->
<!-- Template: Requirements | Level: Template | Prerequisites: None -->
<!-- Related: process/requirements-phase.md, resources/standards.md, examples/simple-feature-spec.md -->

**📍 You are here:** [Main Guide](../../README.md) → [Templates](README.md) → **Requirements Template**

## Quick Navigation
- **📚 Learn Process:** [Requirements Phase Guide](../process/requirements-phase.md) - How to use this template
- **📖 See Example:** [Simple Feature Requirements](../examples/simple-feature-spec.md#requirements-document) - Template in action
- **📋 EARS Reference:** [Standards Guide](../resources/standards.md) - EARS format details
- **➡️ Next Template:** [Design Template](design-template.md) - After requirements are done

---

Use this template to create comprehensive requirements documents using the EARS (Easy Approach to Requirements Syntax) format.

## Document Information

- **Feature Name**: [Your Feature Name]
- **Version**: 1.0
- **Date**: [Current Date]
- **Author**: [Your Name]
- **Stakeholders**: [List key stakeholders]

## Introduction

[Provide a clear, concise overview of the feature. Explain what problem it solves and why it's needed. Keep this section to 2-3 paragraphs maximum.]

### Feature Summary
[One sentence summary of what this feature does]

### Business Value
[Explain the business value and expected outcomes]

### Scope
[Define what is included and excluded from this feature]

## Requirements

### Requirement 1: [Requirement Title]

**User Story:** As a [role/user type], I want [desired functionality], so that [benefit/value].

#### Acceptance Criteria

1. WHEN [specific event or trigger] THEN [system name] SHALL [specific system response]
2. IF [condition or state] THEN [system name] SHALL [required behavior]
3. WHILE [ongoing condition] [system name] SHALL [continuous behavior]
4. WHERE [context or location] [system name] SHALL [contextual behavior]

#### Additional Details
- **Priority**: [High/Medium/Low]
- **Complexity**: [High/Medium/Low]
- **Dependencies**: [List any dependencies on other requirements or systems]
- **Assumptions**: [List any assumptions made]

### Requirement 2: [Requirement Title]

**User Story:** As a [role/user type], I want [desired functionality], so that [benefit/value].

#### Acceptance Criteria

1. WHEN [specific event or trigger] THEN [system name] SHALL [specific system response]
2. IF [condition or state] THEN [system name] SHALL [required behavior]

#### Additional Details
- **Priority**: [High/Medium/Low]
- **Complexity**: [High/Medium/Low]
- **Dependencies**: [List any dependencies]
- **Assumptions**: [List any assumptions]

### Requirement 3: [Requirement Title]

**User Story:** As a [role/user type], I want [desired functionality], so that [benefit/value].

#### Acceptance Criteria

1. WHEN [specific event or trigger] THEN [system name] SHALL [specific system response]
2. IF [condition or state] THEN [system name] SHALL [required behavior]

#### Additional Details
- **Priority**: [High/Medium/Low]
- **Complexity**: [High/Medium/Low]
- **Dependencies**: [List any dependencies]
- **Assumptions**: [List any assumptions]

## Non-Functional Requirements

### Performance Requirements
- WHEN [load condition] THEN [system name] SHALL [performance criteria]
- IF [usage scenario] THEN [system name] SHALL [response time requirement]

### Security Requirements
- WHEN [security event] THEN [system name] SHALL [security response]
- IF [authentication condition] THEN [system name] SHALL [access control behavior]

### Usability Requirements
- WHEN [user interaction] THEN [system name] SHALL [usability standard]
- IF [accessibility condition] THEN [system name] SHALL [accessibility compliance]

### Reliability Requirements
- WHEN [failure condition] THEN [system name] SHALL [recovery behavior]
- IF [error state] THEN [system name] SHALL [error handling response]

## Constraints and Assumptions

### Technical Constraints
- [List technical limitations or constraints]
- [Include platform, technology, or integration constraints]

### Business Constraints
- [List business rules or policy constraints]
- [Include budget, timeline, or resource constraints]

### Assumptions
- [List assumptions about user behavior]
- [Include assumptions about system environment]
- [Note assumptions about external dependencies]

## Success Criteria

### Definition of Done
- [ ] All acceptance criteria are met
- [ ] Non-functional requirements are satisfied
- [ ] Integration requirements are fulfilled
- [ ] Testing criteria are passed

### Acceptance Metrics
- [Define measurable success criteria]
- [Include performance benchmarks]
- [Specify quality gates]

## Glossary

| Term | Definition |
|------|------------|
| [Term 1] | [Clear definition] |
| [Term 2] | [Clear definition] |
| [Term 3] | [Clear definition] |

---

## Requirements Review Checklist

Use this checklist to validate your requirements document:

### Completeness
- [ ] All user stories have clear roles, features, and benefits
- [ ] Each requirement has specific acceptance criteria using EARS format
- [ ] Non-functional requirements are addressed
- [ ] Success criteria are defined and measurable

### Quality
- [ ] Requirements are written in active voice
- [ ] Each acceptance criterion is testable
- [ ] Requirements avoid implementation details
- [ ] Terminology is consistent throughout

### EARS Format Validation
- [ ] WHEN statements describe specific events or triggers
- [ ] IF statements describe clear conditions or states
- [ ] WHILE statements describe continuous behaviors
- [ ] WHERE statements describe specific contexts
- [ ] All statements use SHALL for system responses

### Clarity
- [ ] Requirements are unambiguous
- [ ] Technical jargon is explained in glossary
- [ ] Stakeholders can understand all requirements
- [ ] No conflicting requirements exist

### Traceability
- [ ] Requirements are numbered and organized
- [ ] Dependencies between requirements are clear
- [ ] Requirements link to business objectives
- [ ] Assumptions and constraints are documented

---

## Tips for Writing Good Requirements

### Do's
- ✅ Use active voice and specific language
- ✅ Focus on what the system should do, not how
- ✅ Make each requirement testable and verifiable
- ✅ Include both positive and negative scenarios
- ✅ Consider edge cases and error conditions

### Don'ts
- ❌ Don't use vague terms like "user-friendly" or "fast"
- ❌ Don't combine multiple requirements in one statement
- ❌ Don't specify implementation details
- ❌ Don't use subjective or unmeasurable criteria
- ❌ Don't forget to consider non-functional aspects

### Common EARS Patterns

**Event-Driven (WHEN)**
- User actions: "WHEN user clicks submit button"
- System events: "WHEN data sync completes"
- Time-based: "WHEN daily backup runs"

**Condition-Based (IF)**
- State checks: "IF user is authenticated"
- Data validation: "IF input is invalid"
- Permission checks: "IF user has admin role"

**Continuous (WHILE)**
- Ongoing processes: "WHILE file is uploading"
- Monitoring: "WHILE system is running"
- Real-time updates: "WHILE user is typing"

**Contextual (WHERE)**
- Platform-specific: "WHERE application runs on mobile"
- Environment-specific: "WHERE system is in production"
- Location-specific: "WHERE user is in restricted area"

---

[← Back to Templates](README.md) | [Design Template →](design-template.md)