# Case Studies: Troubleshooting and Pitfalls

<!-- Navigation Metadata -->
<!-- Example: Case Studies | Level: Troubleshooting | Prerequisites: simple-feature-spec.md -->
<!-- Related: process/README.md, prompting/best-practices.md, execution/troubleshooting.md -->

**📍 You are here:** [Main Guide](../../README.md) → [Examples](README.md) → **Case Studies**

## Quick Navigation
- **📖 Learn Basics:** [Simple Feature Specs](simple-feature-spec.md) - See good examples first
- **📋 Process Help:** [Process Guide](../process/README.md) - Avoid pitfalls with systematic approach
- **💬 Better Prompting:** [Best Practices](../prompting/best-practices.md) - Communicate more effectively
- **⚡ Execution Issues:** [Troubleshooting Guide](../execution/troubleshooting.md) - Fix implementation problems

---

This section documents common mistakes, failed approaches, and lessons learned from real-world spec-driven development experiences. Learning from these pitfalls can help you avoid similar issues and recover when problems arise.

## Common Pitfalls and How to Avoid Them

### Requirements Phase Pitfalls

#### Pitfall 1: Vague or Ambiguous Requirements

**What Went Wrong:**
A team specified a requirement as "The system should be fast and user-friendly." This led to disagreements during implementation about what constituted acceptable performance and usability.

**Example of Poor Requirement:**
```markdown
### Requirement 1
**User Story:** As a user, I want the application to be fast, so that I have a good experience.

#### Acceptance Criteria
1. WHEN using the application THEN it should be fast
2. WHEN navigating THEN it should be responsive
```

**What Should Have Been Done:**
```markdown
### Requirement 1
**User Story:** As a user, I want page loads to complete quickly, so that I can accomplish my tasks efficiently.

#### Acceptance Criteria
1. WHEN loading the main dashboard THEN the page SHALL render within 2 seconds
2. WHEN clicking navigation links THEN the new page SHALL load within 1.5 seconds
3. WHEN submitting forms THEN the system SHALL provide feedback within 500ms
4. IF network conditions are poor THEN the system SHALL show loading indicators after 1 second
```

**Recovery Strategy:**
- Stop implementation and return to requirements clarification
- Define specific, measurable criteria for all subjective terms
- Get stakeholder agreement on concrete metrics
- Update the requirements document before proceeding

#### Pitfall 2: Missing Edge Cases and Error Scenarios

**What Went Wrong:**
A user authentication system was specified without considering password reset, account lockout, or concurrent login scenarios. This led to security vulnerabilities and poor user experience.

**Example of Incomplete Requirements:**
```markdown
### Requirement 1
**User Story:** As a user, I want to log in with email and password, so that I can access my account.

#### Acceptance Criteria
1. WHEN providing correct credentials THEN the system SHALL authenticate the user
2. WHEN providing incorrect credentials THEN the system SHALL show an error
```

**What Should Have Been Done:**
```markdown
### Requirement 1
**User Story:** As a user, I want to log in securely with email and password, so that I can access my account while maintaining security.

#### Acceptance Criteria
1. WHEN providing correct credentials THEN the system SHALL authenticate and create a session
2. WHEN providing incorrect credentials THEN the system SHALL show a generic error message
3. WHEN failing login 5 times THEN the system SHALL temporarily lock the account for 15 minutes
4. WHEN already logged in elsewhere THEN the system SHALL handle concurrent sessions appropriately
5. IF the account is locked THEN the system SHALL provide password reset options
6. WHEN the session expires THEN the system SHALL require re-authentication
```

**Recovery Strategy:**
- Conduct a systematic review of all failure scenarios
- Consider the "unhappy path" for every user story
- Add security and edge case requirements
- Review with security experts if handling sensitive data

#### Pitfall 3: Technology-Specific Requirements

**What Went Wrong:**
Requirements specified "The system must use React and Node.js" instead of focusing on functional needs. This limited design flexibility and made the spec less reusable.

**Example of Technology-Coupled Requirements:**
```markdown
### Requirement 1
**User Story:** As a developer, I want to use React for the frontend, so that the UI is interactive.

#### Acceptance Criteria
1. WHEN building the UI THEN it SHALL use React components
2. WHEN handling state THEN it SHALL use Redux
```

**What Should Have Been Done:**
```markdown
### Requirement 1
**User Story:** As a user, I want an interactive web interface, so that I can efficiently manage my data.

#### Acceptance Criteria
1. WHEN interacting with forms THEN changes SHALL be reflected immediately without page refresh
2. WHEN data updates THEN the interface SHALL update automatically
3. WHEN using the interface THEN it SHALL work on modern web browsers
4. IF JavaScript is disabled THEN core functionality SHALL still be accessible
```

**Recovery Strategy:**
- Separate functional requirements from implementation choices
- Move technology decisions to the design phase
- Focus requirements on user value and business outcomes
- Allow design phase to evaluate technology options

### Design Phase Pitfalls

#### Pitfall 4: Over-Engineering from the Start

**What Went Wrong:**
A simple content management feature was designed with microservices, event sourcing, and complex caching layers before understanding actual usage patterns.

**Example of Over-Engineered Design:**
```markdown
## Architecture
The content management system will use:
- 5 microservices with separate databases
- Event sourcing for all data changes
- Redis cluster for distributed caching
- Message queues for all inter-service communication
- Elasticsearch for content search
```

**What Should Have Been Done:**
```markdown
## Architecture
The content management system will start with:
- Single service with clear module boundaries
- Traditional database with proper indexing
- Simple caching for frequently accessed content
- Direct API calls between modules
- Database full-text search initially

## Future Scaling Considerations
- Module boundaries designed to support future service extraction
- Database schema designed to support event sourcing if needed
- Caching layer abstracted to support distributed caching
- API design supports future microservices architecture
```

**Recovery Strategy:**
- Start with the simplest design that meets requirements
- Design for future scalability without implementing it initially
- Plan clear upgrade paths for when complexity is needed
- Focus on solving current problems, not hypothetical future ones

#### Pitfall 5: Insufficient Error Handling Design

**What Went Wrong:**
A payment processing system design focused on the happy path but didn't adequately plan for network failures, timeout scenarios, or partial payment states.

**Example of Incomplete Error Handling:**
```markdown
## Payment Processing Flow
1. Validate payment information
2. Charge payment method
3. Update order status
4. Send confirmation email
```

**What Should Have Been Done:**
```markdown
## Payment Processing Flow

### Happy Path
1. Validate payment information
2. Charge payment method
3. Update order status
4. Send confirmation email

### Error Scenarios
- **Validation Failure**: Return specific field errors, log attempt
- **Payment Declined**: Store attempt, offer alternative payment methods
- **Network Timeout**: Implement retry with exponential backoff
- **Partial Charge**: Implement idempotency keys, reconciliation process
- **Database Failure**: Queue status updates, implement eventual consistency
- **Email Failure**: Queue email for retry, don't fail the payment

### Recovery Mechanisms
- Automatic retry for transient failures
- Manual reconciliation tools for payment discrepancies
- Customer service tools for payment issue resolution
- Monitoring and alerting for payment system health
```

**Recovery Strategy:**
- Map out all possible failure points in the system
- Design specific handling for each type of failure
- Implement monitoring and alerting for error conditions
- Create manual recovery procedures for complex failures

#### Pitfall 6: Ignoring Non-Functional Requirements

**What Went Wrong:**
A data processing system was designed without considering performance, security, or scalability requirements, leading to production issues.

**Example of Missing Non-Functional Considerations:**
```markdown
## Data Processing Design
The system will:
- Read data from CSV files
- Transform data according to business rules
- Store results in database
```

**What Should Have Been Done:**
```markdown
## Data Processing Design

### Functional Design
- Read data from CSV files with configurable batch sizes
- Transform data using pluggable business rule engine
- Store results with transaction management

### Non-Functional Design
- **Performance**: Process 10,000 records per minute minimum
- **Scalability**: Support horizontal scaling for larger datasets
- **Security**: Encrypt data at rest and in transit
- **Reliability**: Implement checkpointing for recovery from failures
- **Monitoring**: Track processing metrics and error rates
- **Maintainability**: Support hot-swapping of business rules
```

**Recovery Strategy:**
- Review requirements for implicit non-functional needs
- Add performance, security, and scalability considerations
- Design monitoring and observability from the start
- Plan for operational concerns like deployment and maintenance

### Tasks Phase Pitfalls

#### Pitfall 7: Tasks Too Large or Vague

**What Went Wrong:**
Implementation tasks were defined as "Implement user management" and "Build the API," leading to unclear progress tracking and difficulty estimating work.

**Example of Poor Task Definition:**
```markdown
- [ ] 1. Implement user management
  - Build all user-related functionality
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2_

- [ ] 2. Build the API
  - Create REST endpoints for all features
  - _Requirements: 3.1, 3.2, 4.1_
```

**What Should Have Been Done:**
```markdown
- [ ] 1. Create user data model and validation
  - Implement User interface with TypeScript types
  - Create email validation with regex pattern
  - Add password strength validation (8+ chars, mixed case, numbers)
  - Write unit tests for validation functions
  - _Requirements: 1.1, 1.2_

- [ ] 2. Implement user registration endpoint
  - Create POST /api/users endpoint with request validation
  - Add duplicate email checking with appropriate error response
  - Implement password hashing using bcrypt
  - Write integration tests for registration flow
  - _Requirements: 1.1, 1.3_

- [ ] 3. Build user authentication endpoint
  - Create POST /api/auth/login endpoint
  - Implement credential verification and JWT token generation
  - Add rate limiting for login attempts
  - Write integration tests for authentication flow
  - _Requirements: 2.1, 2.2_
```

**Recovery Strategy:**
- Break large tasks into specific, testable units
- Each task should be completable in 1-2 days maximum
- Include specific deliverables and acceptance criteria
- Reference specific requirements for each task

#### Pitfall 8: Missing Dependencies and Sequencing

**What Went Wrong:**
Tasks were defined without considering dependencies, leading to blocked work and inefficient development flow.

**Example of Poor Task Sequencing:**
```markdown
- [ ] 1. Build user interface components
- [ ] 2. Implement API endpoints
- [ ] 3. Create database schema
- [ ] 4. Set up authentication middleware
```

**What Should Have Been Done:**
```markdown
- [ ] 1. Set up project infrastructure
  - Create database schema and migrations
  - Set up development environment and dependencies
  - Configure testing framework
  - _Requirements: Foundation for all other tasks_

- [ ] 2. Implement core data models
  - Create User model with validation
  - Implement database repository layer
  - Write unit tests for data models
  - _Requirements: 1.1, 1.2_

- [ ] 3. Build authentication services
  - Implement password hashing and verification
  - Create JWT token generation and validation
  - Write unit tests for authentication logic
  - _Requirements: 2.1, 2.2_

- [ ] 4. Create API endpoints
  - Build user registration endpoint using authentication services
  - Implement login endpoint with token generation
  - Add authentication middleware for protected routes
  - Write integration tests for complete API flows
  - _Requirements: 1.1, 2.1, 3.1_

- [ ] 5. Build user interface components
  - Create registration form with validation
  - Implement login form with error handling
  - Add authenticated user dashboard
  - Write component tests and user interaction tests
  - _Requirements: 3.2, 3.3_
```

**Recovery Strategy:**
- Map out dependencies between tasks
- Sequence tasks so that each builds on completed work
- Identify critical path items that block other work
- Consider parallel work streams where possible

#### Pitfall 9: Insufficient Testing Strategy

**What Went Wrong:**
Tasks focused only on feature implementation without adequate testing, leading to bugs discovered late in development.

**Example of Testing-Light Tasks:**
```markdown
- [ ] 1. Implement user registration
  - Create registration form
  - Add backend validation
  - Store user in database
  - _Requirements: 1.1_

- [ ] 2. Add user login
  - Create login form
  - Verify credentials
  - Create user session
  - _Requirements: 2.1_
```

**What Should Have Been Done:**
```markdown
- [ ] 1. Implement user registration with comprehensive testing
  - Create User model with validation rules
  - Write unit tests for User model validation edge cases
  - Implement registration API endpoint with error handling
  - Write integration tests for registration flow including error scenarios
  - Create registration form with client-side validation
  - Write end-to-end tests for complete registration user journey
  - _Requirements: 1.1_

- [ ] 2. Add user login with security testing
  - Implement credential verification with secure password comparison
  - Write unit tests for authentication logic including timing attacks
  - Create login API endpoint with rate limiting
  - Write integration tests for login flow including brute force scenarios
  - Build login form with proper error handling
  - Write end-to-end tests for login user journey and security measures
  - _Requirements: 2.1_
```

**Recovery Strategy:**
- Add testing requirements to every implementation task
- Include unit, integration, and end-to-end testing
- Consider security testing for sensitive functionality
- Plan for both positive and negative test scenarios

## Recovery Strategies for Common Problems

### When Requirements Are Unclear Mid-Implementation

**Symptoms:**
- Developers asking frequent clarification questions
- Implementation decisions being made without stakeholder input
- Features being built that don't match user expectations

**Recovery Steps:**
1. **Stop Implementation**: Pause coding work to prevent building the wrong thing
2. **Document Assumptions**: List all assumptions being made about unclear requirements
3. **Stakeholder Review**: Schedule immediate review with business stakeholders
4. **Clarify and Update**: Update requirements document with specific, measurable criteria
5. **Impact Assessment**: Evaluate what work needs to be redone
6. **Resume with Clarity**: Continue implementation only after requirements are clear

### When Design Doesn't Support Requirements

**Symptoms:**
- Implementation tasks seem impossible or overly complex
- Performance requirements can't be met with current design
- Security or scalability concerns emerge during implementation

**Recovery Steps:**
1. **Identify Root Cause**: Determine which requirements the design fails to support
2. **Design Review**: Conduct thorough review of design decisions
3. **Alternative Evaluation**: Research alternative architectural approaches
4. **Stakeholder Communication**: Explain trade-offs and get input on priorities
5. **Design Revision**: Update design document with new approach
6. **Task Adjustment**: Revise implementation tasks to match new design

### When Implementation Tasks Are Blocked

**Symptoms:**
- Tasks can't be started due to missing dependencies
- Work is proceeding in wrong order
- Team members are waiting for others to complete prerequisite work

**Recovery Steps:**
1. **Dependency Mapping**: Create visual map of all task dependencies
2. **Critical Path Analysis**: Identify which tasks are blocking the most other work
3. **Parallel Work Identification**: Find tasks that can be done simultaneously
4. **Task Resequencing**: Reorder tasks to optimize workflow
5. **Resource Reallocation**: Assign team members to unblocked work
6. **Regular Check-ins**: Implement daily standups to catch blocking issues early

### When Quality Issues Emerge Late

**Symptoms:**
- Bugs discovered during integration testing
- Performance problems in production-like environments
- Security vulnerabilities found during review

**Recovery Steps:**
1. **Issue Triage**: Categorize problems by severity and impact
2. **Root Cause Analysis**: Determine why issues weren't caught earlier
3. **Testing Gap Analysis**: Identify what testing was missing
4. **Process Improvement**: Add missing testing types to future tasks
5. **Immediate Fixes**: Address critical issues blocking progress
6. **Prevention Planning**: Update spec process to prevent similar issues

## Lessons Learned from Failed Approaches

### Case Study 1: The Over-Specified Spec

**Background:**
A team created a 200-page specification document that attempted to define every possible detail of a content management system before any implementation began.

**What Went Wrong:**
- Specification took 3 months to write
- Requirements changed during the long specification phase
- Implementation revealed many specification assumptions were wrong
- Team spent more time updating documentation than building features

**Key Lessons:**
- Specifications should be detailed enough to guide implementation, not replace thinking
- Start with core functionality and iterate
- Validate assumptions with prototypes before full specification
- Keep specifications living documents that evolve with understanding

### Case Study 2: The Technology-First Design

**Background:**
A team decided to use microservices, event sourcing, and GraphQL for a simple inventory management system because these were "modern" technologies.

**What Went Wrong:**
- Development time increased 3x due to complexity
- Simple features required changes across multiple services
- Debugging became extremely difficult
- Team spent more time on infrastructure than business logic

**Key Lessons:**
- Choose technology based on requirements, not trends
- Start simple and add complexity only when needed
- Consider team expertise when making technology choices
- Focus on solving business problems, not showcasing technology

### Case Study 3: The Missing Monitoring Spec

**Background:**
A data processing pipeline was thoroughly specified for functionality but had no monitoring, logging, or observability requirements.

**What Went Wrong:**
- Production issues were impossible to debug
- No visibility into system performance or health
- Customer issues couldn't be traced to root causes
- System reliability was poor due to lack of operational insight

**Key Lessons:**
- Operational requirements are as important as functional ones
- Monitoring and observability should be specified from the start
- Consider the full lifecycle of the system, not just initial functionality
- Include operational runbooks and troubleshooting procedures

## Prevention Strategies

### Requirements Phase Prevention

1. **Use Concrete Examples**: Always include specific examples of expected behavior
2. **Define Acceptance Tests**: Write testable acceptance criteria for every requirement
3. **Consider Edge Cases**: Systematically think through error scenarios and boundary conditions
4. **Stakeholder Review**: Get explicit approval from business stakeholders before proceeding
5. **Prototype Validation**: Build small prototypes to validate assumptions

### Design Phase Prevention

1. **Start Simple**: Begin with the simplest design that meets requirements
2. **Plan for Evolution**: Design for future needs without implementing them initially
3. **Consider Operations**: Include monitoring, logging, and maintenance in design
4. **Review Trade-offs**: Explicitly document design decisions and their trade-offs
5. **Validate with Implementation**: Build proof-of-concept for complex design decisions

### Tasks Phase Prevention

1. **Right-Size Tasks**: Each task should be completable in 1-2 days
2. **Include Testing**: Every implementation task should include corresponding tests
3. **Map Dependencies**: Understand and document task dependencies
4. **Plan Integration**: Include tasks for integrating components together
5. **Consider Deployment**: Include tasks for deployment and operational concerns

## Quick Reference: Warning Signs

### Requirements Warning Signs
- ❌ Requirements use subjective terms without definition ("fast", "user-friendly")
- ❌ No error scenarios or edge cases considered
- ❌ Technology choices embedded in requirements
- ❌ Stakeholders haven't reviewed or approved requirements

### Design Warning Signs
- ❌ Design is much more complex than requirements suggest
- ❌ No consideration of non-functional requirements
- ❌ No error handling or failure scenarios planned
- ❌ Design decisions not justified or documented

### Tasks Warning Signs
- ❌ Tasks are too large (more than 2-3 days of work)
- ❌ No testing included in implementation tasks
- ❌ Dependencies between tasks not considered
- ❌ No integration or deployment tasks included

---

[← Complex System Examples](complex-system-spec.md) | [Back to Examples Overview](README.md)