# Requirements Phase Documentation

<!-- Navigation Metadata -->
<!-- Phase: Requirements | Level: Detailed Guide | Prerequisites: methodology/README.md -->
<!-- Related: templates/requirements-template.md, resources/standards.md, examples/simple-feature-spec.md -->

**📍 You are here:** [Main Guide](../../README.md) → [Process Guide](README.md) → **Requirements Phase**

## Quick Navigation
- **🎯 Get Started:** [Requirements Template](../templates/requirements-template.md) - Ready-to-use template
- **📖 See Example:** [Simple Feature Spec](../examples/simple-feature-spec.md) - Complete requirements example
- **📚 Learn EARS:** [Standards Reference](../resources/standards.md) - EARS format details
- **➡️ Next Phase:** [Design Phase](design-phase.md) - After requirements are approved

## Phase Navigation
- **Previous:** [Process Overview](README.md) - Three-phase workflow
- **Current:** **Requirements Phase** - Transform ideas into structured requirements
- **Next:** [Design Phase](design-phase.md) - Create technical architecture
- **Final:** [Tasks Phase](tasks-phase.md) - Break down into implementation steps

---

## Overview

The Requirements Phase is the foundation of spec-driven development, where rough feature ideas are transformed into clear, testable requirements using the EARS (Easy Approach to Requirements Syntax) format. This phase ensures all stakeholders have a shared understanding of what needs to be built before moving to design and implementation.

## Purpose and Goals

The requirements phase serves to:
- Transform vague feature ideas into concrete, measurable requirements
- Establish clear acceptance criteria for feature success
- Create a shared understanding between stakeholders
- Provide a foundation for design and implementation decisions
- Enable effective testing and validation strategies

## Step-by-Step Process

### Step 1: Initial Requirements Generation

**Objective**: Create a first draft of requirements based on the feature idea

**Process**:
1. **Analyze the Feature Idea**: Break down the core concept into user-facing functionality
2. **Identify User Roles**: Determine who will interact with the feature
3. **Define User Stories**: Create user stories in the format "As a [role], I want [feature], so that [benefit]"
4. **Generate Acceptance Criteria**: Write EARS-format requirements for each user story

**Key Principles**:
- Start with what the user experiences, not technical implementation
- Focus on observable, testable behaviors
- Consider edge cases and error scenarios
- Think about the complete user journey

### Step 2: Requirements Structure and Format

**Document Structure**:
```markdown
# Requirements Document

## Introduction
[Brief overview of the feature and its purpose]

## Requirements

### Requirement 1
**User Story:** As a [role], I want [feature], so that [benefit]

#### Acceptance Criteria
1. WHEN [event] THEN [system] SHALL [response]
2. IF [precondition] THEN [system] SHALL [response]
3. WHEN [event] AND [condition] THEN [system] SHALL [response]

### Requirement 2
[Continue with additional requirements...]
```

**EARS Format Guidelines**:
- **WHEN**: Describes triggering events or conditions
- **IF**: Describes preconditions that must be met
- **THEN**: Describes the system's required response
- **SHALL**: Indicates mandatory behavior (use consistently)
- **AND/OR**: Combines conditions when necessary

### Step 3: Requirements Validation

**Validation Criteria**:
- [ ] Each requirement is testable and measurable
- [ ] Requirements cover normal, edge, and error cases
- [ ] User stories provide clear business value
- [ ] Acceptance criteria are specific and unambiguous
- [ ] Requirements are independent and don't conflict
- [ ] All user roles and interactions are addressed

**Common Validation Questions**:
- Can this requirement be tested automatically?
- Is the expected behavior clearly defined?
- Are there any assumptions that need to be made explicit?
- What happens when things go wrong?
- Are there any missing user scenarios?

### Step 4: Iterative Refinement

**Refinement Process**:
1. **Review with Stakeholders**: Get feedback on completeness and accuracy
2. **Identify Gaps**: Look for missing scenarios or unclear requirements
3. **Clarify Ambiguities**: Resolve any vague or conflicting requirements
4. **Add Missing Details**: Include edge cases and error handling
5. **Validate Business Value**: Ensure each requirement serves a clear purpose

**Iteration Guidelines**:
- Make one focused change at a time
- Always ask for explicit approval after changes
- Document the reasoning behind requirement decisions
- Keep requirements at the right level of detail (not too high, not too low)

## EARS Format Deep Dive

### Basic EARS Patterns

**Simple Event-Response**:
```
WHEN [user clicks submit button] THEN [system] SHALL [validate form data]
```

**Conditional Behavior**:
```
IF [user is authenticated] THEN [system] SHALL [display user dashboard]
```

**Complex Conditions**:
```
WHEN [user submits form] AND [all required fields are completed] THEN [system] SHALL [process the submission]
```

**Error Handling**:
```
WHEN [user submits invalid data] THEN [system] SHALL [display specific error messages]
```

### Advanced EARS Patterns

**State-Based Requirements**:
```
WHEN [system is in maintenance mode] THEN [system] SHALL [display maintenance message to all users]
```

**Performance Requirements**:
```
WHEN [user requests data] THEN [system] SHALL [respond within 2 seconds]
```

**Security Requirements**:
```
IF [user session expires] THEN [system] SHALL [redirect to login page]
```

## Examples of Well-Formed Requirements

### Example 1: User Authentication Feature

**User Story**: As a new user, I want to create an account, so that I can access personalized features.

**Acceptance Criteria**:
1. WHEN user provides valid email and password THEN system SHALL create new account
2. WHEN user provides existing email THEN system SHALL display "email already registered" error
3. WHEN user provides invalid email format THEN system SHALL display "invalid email format" error
4. WHEN user provides password shorter than 8 characters THEN system SHALL display "password too short" error
5. WHEN account creation succeeds THEN system SHALL send confirmation email
6. WHEN account creation succeeds THEN system SHALL redirect to welcome page

### Example 2: Data Validation Feature

**User Story**: As a user, I want my input to be validated, so that I don't submit incorrect information.

**Acceptance Criteria**:
1. WHEN user enters data in required field THEN system SHALL remove any error highlighting
2. WHEN user submits form with empty required fields THEN system SHALL highlight missing fields in red
3. WHEN user enters invalid data format THEN system SHALL display format requirements below field
4. WHEN all validation passes THEN system SHALL enable submit button
5. IF validation fails THEN system SHALL keep submit button disabled

### Example 3: File Upload Feature

**User Story**: As a user, I want to upload files, so that I can share documents with my team.

**Acceptance Criteria**:
1. WHEN user selects file under 10MB THEN system SHALL accept file for upload
2. WHEN user selects file over 10MB THEN system SHALL display "file too large" error
3. WHEN user selects unsupported file type THEN system SHALL display "unsupported format" error
4. WHEN upload is in progress THEN system SHALL display progress indicator
5. WHEN upload completes successfully THEN system SHALL display success message
6. WHEN upload fails THEN system SHALL display retry option
7. IF user is not authenticated THEN system SHALL redirect to login before upload

## Common Pitfalls and How to Avoid Them

### Pitfall 1: Vague Requirements
**Problem**: "System should be fast"
**Solution**: "WHEN user requests data THEN system SHALL respond within 2 seconds"

### Pitfall 2: Implementation Details in Requirements
**Problem**: "System shall use Redis for caching"
**Solution**: "WHEN user requests frequently accessed data THEN system SHALL return cached results"

### Pitfall 3: Missing Error Cases
**Problem**: Only defining happy path scenarios
**Solution**: Always include WHEN/IF statements for error conditions

### Pitfall 4: Conflicting Requirements
**Problem**: Requirements that contradict each other
**Solution**: Review all requirements together and resolve conflicts explicitly

### Pitfall 5: Untestable Requirements
**Problem**: "System should be user-friendly"
**Solution**: "WHEN new user completes onboarding THEN system SHALL require no more than 3 clicks to reach main features"

## Quality Checklist

Before moving to the design phase, verify:

**Completeness**:
- [ ] All user roles are identified and addressed
- [ ] Normal, edge, and error cases are covered
- [ ] All user interactions have defined system responses
- [ ] Business rules and constraints are captured

**Clarity**:
- [ ] Each requirement uses precise, unambiguous language
- [ ] Technical jargon is avoided or clearly defined
- [ ] Requirements are written from user perspective
- [ ] Expected behaviors are specific and measurable

**Consistency**:
- [ ] EARS format is used consistently throughout
- [ ] Terminology is consistent across requirements
- [ ] Requirements don't contradict each other
- [ ] Similar scenarios are handled similarly

**Testability**:
- [ ] Each requirement can be verified through testing
- [ ] Success criteria are observable and measurable
- [ ] Requirements specify both inputs and expected outputs
- [ ] Acceptance criteria are specific enough to guide test creation

## Troubleshooting Common Issues

### Issue: Requirements Keep Growing
**Symptoms**: New requirements constantly being added during review
**Solution**: Set a scope boundary early and document out-of-scope items for future iterations

### Issue: Stakeholder Disagreement
**Symptoms**: Different stakeholders want conflicting functionality
**Solution**: Facilitate discussion to understand underlying needs and find compromise solutions

### Issue: Requirements Too Technical
**Symptoms**: Requirements focus on implementation rather than user needs
**Solution**: Reframe requirements from user perspective and move technical details to design phase

### Issue: Requirements Too Vague
**Symptoms**: Acceptance criteria that can't be tested or measured
**Solution**: Ask "How would we know this requirement is met?" and make criteria more specific

## Next Steps

Once requirements are complete and approved:
1. **Transition to Design Phase**: Use requirements as foundation for system design
2. **Maintain Traceability**: Ensure design decisions map back to specific requirements
3. **Keep Requirements Updated**: Update requirements if design reveals gaps or conflicts
4. **Prepare for Implementation**: Requirements will guide task breakdown and testing strategy

The requirements phase sets the foundation for everything that follows. Taking time to get requirements right saves significant effort in design and implementation phases.