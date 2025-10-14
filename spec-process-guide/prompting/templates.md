# Prompt Templates and Patterns

This guide provides specific prompt templates for each phase of spec development, with variations for different feature types and complexity levels.

## Template Structure

Each template follows this pattern:
- **Context Setting**: Establish project background and constraints
- **Phase-Specific Instructions**: Clear guidance for the current phase
- **Output Format**: Specific formatting requirements
- **Validation Criteria**: How to evaluate the response

## Requirements Phase Templates

### Basic Feature Requirements

```
I want to create a spec for [FEATURE_NAME]. Here's my initial idea:

[BRIEF_FEATURE_DESCRIPTION]

Please help me create comprehensive requirements using the EARS format. Focus on:
- User stories that capture the core value proposition
- Acceptance criteria that are testable and specific
- Edge cases and error scenarios
- Integration points with existing systems

The feature should serve [TARGET_USER_TYPE] and solve [CORE_PROBLEM].
```

### Complex System Requirements

```
I'm planning a [SYSTEM_TYPE] that needs to handle [CORE_FUNCTIONALITY]. 

Key constraints:
- Performance: [PERFORMANCE_REQUIREMENTS]
- Scale: [EXPECTED_USAGE_PATTERNS]
- Integration: [EXISTING_SYSTEMS_TO_INTEGRATE]
- Compliance: [REGULATORY_OR_BUSINESS_REQUIREMENTS]

Please help me break this down into well-structured requirements using EARS format. Pay special attention to:
- System boundaries and interfaces
- Non-functional requirements
- Data flow and processing requirements
- Security and compliance considerations
```

### API/Service Requirements

```
I need to design an API for [API_PURPOSE]. The API should:

Core functionality:
- [PRIMARY_OPERATIONS]
- [SECONDARY_OPERATIONS]

Technical context:
- Expected consumers: [WHO_WILL_USE_IT]
- Data sources: [WHERE_DATA_COMES_FROM]
- Performance needs: [RESPONSE_TIME_REQUIREMENTS]

Please create requirements that cover:
- Endpoint specifications and data models
- Authentication and authorization
- Error handling and status codes
- Rate limiting and usage policies
```

## Design Phase Templates

### Architecture Design

```
Based on the requirements we've established, I need a comprehensive design for [FEATURE_NAME].

Requirements summary: [BRIEF_RECAP_OF_KEY_REQUIREMENTS]

Please create a design that addresses:
- Overall architecture and component relationships
- Data models and their relationships
- API interfaces and contracts
- Error handling strategies
- Testing approach

Consider these technical constraints:
- Technology stack: [CURRENT_TECH_STACK]
- Performance requirements: [KEY_PERFORMANCE_NEEDS]
- Integration points: [SYSTEMS_TO_INTEGRATE_WITH]
```

### Database Design Focus

```
I need a detailed database design for [FEATURE_NAME] based on our requirements.

Key data entities from requirements:
- [ENTITY_1]: [BRIEF_DESCRIPTION]
- [ENTITY_2]: [BRIEF_DESCRIPTION]
- [ENTITY_3]: [BRIEF_DESCRIPTION]

Please design:
- Entity relationship diagrams
- Table schemas with appropriate constraints
- Indexing strategy for performance
- Data migration considerations
- Backup and recovery approach

Database context: [CURRENT_DATABASE_TECHNOLOGY]
```

### UI/UX Design Focus

```
Based on our requirements, I need a user experience design for [FEATURE_NAME].

User context:
- Primary users: [USER_TYPES]
- Usage patterns: [HOW_THEY_WILL_USE_IT]
- Device/platform: [WHERE_THEY_ACCESS_IT]

Please design:
- User flow diagrams
- Interface mockups or wireframes
- Interaction patterns
- Accessibility considerations
- Error state handling
```

## Tasks Phase Templates

### Implementation Planning

```
Now that we have the design approved, please break it down into actionable coding tasks.

Design summary: [KEY_DESIGN_COMPONENTS]

Create an implementation plan that:
- Follows test-driven development principles
- Builds incrementally with early validation
- Sequences tasks to minimize dependencies
- Includes specific file/component creation steps

Each task should:
- Reference specific requirements it addresses
- Be completable by a coding agent
- Build on previous tasks
- Include testing considerations
```

### Refactoring/Migration Planning

```
I need to refactor [EXISTING_SYSTEM] to implement [NEW_FEATURE] based on our design.

Current system context:
- Existing codebase: [BRIEF_DESCRIPTION]
- Technologies used: [CURRENT_TECH_STACK]
- Areas that need changes: [COMPONENTS_TO_MODIFY]

Create tasks that:
- Minimize disruption to existing functionality
- Allow for incremental rollout
- Include comprehensive testing at each step
- Handle data migration if needed
```

## Complexity-Based Variations

### Simple Feature (< 5 requirements)

Use concise templates focusing on:
- Core user story and acceptance criteria
- Basic architecture decisions
- Straightforward task breakdown

### Medium Feature (5-15 requirements)

Include additional sections for:
- Multiple user personas
- Integration considerations
- Performance and scalability
- More detailed task sequencing

### Complex Feature (15+ requirements)

Expand templates to cover:
- System-wide impact analysis
- Detailed technical research needs
- Phased implementation approach
- Risk assessment and mitigation

## Communication Patterns

### Context Preservation

```
Continuing from our previous discussion about [FEATURE_NAME], I'd like to [SPECIFIC_REQUEST].

Previous context:
- [KEY_POINT_1]
- [KEY_POINT_2]
- [KEY_POINT_3]

Please [SPECIFIC_ACTION] while maintaining consistency with what we've established.
```

### Feedback Integration

```
I've reviewed the [REQUIREMENTS/DESIGN/TASKS] and have some feedback:

Changes needed:
1. [SPECIFIC_CHANGE_1] - [REASON]
2. [SPECIFIC_CHANGE_2] - [REASON]
3. [SPECIFIC_CHANGE_3] - [REASON]

Please update the document to incorporate these changes while maintaining the overall structure and quality.
```

### Clarification Requests

```
I need clarification on [SPECIFIC_ASPECT] from the [REQUIREMENTS/DESIGN/TASKS].

Specifically:
- [QUESTION_1]
- [QUESTION_2]
- [QUESTION_3]

Please provide detailed explanations and update the document if needed to make these points clearer.
```

## Quality Validation Prompts

### Requirements Review

```
Please review the requirements document for [FEATURE_NAME] and check:

- Are all user stories complete with clear acceptance criteria?
- Do the requirements use proper EARS format?
- Are edge cases and error scenarios covered?
- Is the scope clearly defined and bounded?
- Are there any missing integration points?

Provide specific feedback on any issues found.
```

### Design Review

```
Please review the design document for [FEATURE_NAME] and validate:

- Does the architecture address all requirements?
- Are the component interfaces well-defined?
- Is the error handling strategy comprehensive?
- Are performance considerations addressed?
- Is the testing approach adequate?

Highlight any gaps or inconsistencies.
```

### Tasks Review

```
Please review the implementation plan for [FEATURE_NAME] and check:

- Are all tasks actionable by a coding agent?
- Do tasks build incrementally without big jumps?
- Are all requirements covered by the tasks?
- Is the sequencing logical and dependency-aware?
- Are testing tasks integrated throughout?

Suggest improvements for any issues identified.
```

## Troubleshooting Prompts

### When Requirements Are Too Vague

```
The requirements seem too high-level. Please help me break down [SPECIFIC_REQUIREMENT] into more specific, testable acceptance criteria.

Focus on:
- Concrete user actions and system responses
- Measurable success criteria
- Specific error conditions and handling
- Clear boundaries of what's included/excluded
```

### When Design Lacks Detail

```
The design needs more technical detail for [SPECIFIC_COMPONENT]. Please expand on:

- Specific interfaces and data contracts
- Implementation approach and technology choices
- Error handling and edge case management
- Performance considerations and constraints
- Testing strategy for this component
```

### When Tasks Are Too Abstract

```
Some tasks in the implementation plan are too abstract for direct coding. Please break down [SPECIFIC_TASK] into concrete coding steps that specify:

- Exact files or components to create/modify
- Specific functions or classes to implement
- Test cases to write
- Integration points to establish
```

---

[← Back to Prompting Guide](README.md) | [Best Practices →](best-practices.md)