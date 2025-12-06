# Simple Feature Spec Examples

<!-- Navigation Metadata -->
<!-- Example: Simple Features | Level: Complete Examples | Prerequisites: methodology/README.md -->
<!-- Related: templates/README.md, process/README.md, complex-system-spec.md -->

**📍 You are here:** [Main Guide](../../README.md) → [Examples](README.md) → **Simple Feature Specs**

## Quick Navigation
- **📚 Learn First:** [Methodology Overview](../methodology/README.md) - Understand the foundation
- **📝 Use Templates:** [Templates](../templates/README.md) - Create your own specs
- **📋 Follow Process:** [Process Guide](../process/README.md) - Step-by-step instructions
- **🏗️ Complex Examples:** [Complex System Specs](complex-system-spec.md) - More advanced examples

---

This section provides complete spec examples for basic features, demonstrating how the three-phase methodology works in practice. Each example includes the full requirements, design, and tasks documents along with commentary explaining key decisions.

## Example 1: User Authentication System

### Overview
A basic user authentication system that allows users to register, login, and manage their sessions. This example demonstrates how to spec a foundational feature that many applications require.

### Complete Spec Documents

#### Requirements Document

```markdown
# User Authentication System - Requirements

## Introduction
This feature implements a secure user authentication system that allows users to create accounts, log in securely, and maintain authenticated sessions. The system will handle user registration, login validation, session management, and basic security measures.

## Requirements

### Requirement 1
**User Story:** As a new user, I want to create an account with email and password, so that I can access the application's protected features.

#### Acceptance Criteria
1. WHEN a user provides valid email and password THEN the system SHALL create a new user account
2. WHEN a user provides an email that already exists THEN the system SHALL return an appropriate error message
3. WHEN a user provides invalid email format THEN the system SHALL validate and reject the input
4. WHEN a user provides a weak password THEN the system SHALL enforce password strength requirements

### Requirement 2
**User Story:** As a registered user, I want to log in with my credentials, so that I can access my account and protected features.

#### Acceptance Criteria
1. WHEN a user provides correct email and password THEN the system SHALL authenticate and create a session
2. WHEN a user provides incorrect credentials THEN the system SHALL return an authentication error
3. WHEN a user attempts multiple failed logins THEN the system SHALL implement rate limiting
4. IF a user is already logged in THEN the system SHALL handle existing session appropriately

### Requirement 3
**User Story:** As a logged-in user, I want my session to be maintained securely, so that I don't have to re-authenticate frequently while staying secure.

#### Acceptance Criteria
1. WHEN a user successfully logs in THEN the system SHALL create a secure session token
2. WHEN a user makes authenticated requests THEN the system SHALL validate the session token
3. WHEN a session expires THEN the system SHALL require re-authentication
4. WHEN a user logs out THEN the system SHALL invalidate the session token
```

#### Design Document

```markdown
# User Authentication System - Design

## Overview
The authentication system will be implemented using JWT tokens for stateless authentication, with secure password hashing using bcrypt. The system will include registration, login, session validation, and logout functionality with appropriate security measures.

## Architecture
- **Authentication Service**: Core business logic for user management
- **Password Service**: Secure password hashing and validation
- **Token Service**: JWT token generation and validation
- **User Repository**: Data access layer for user storage
- **Authentication Middleware**: Request validation for protected routes

## Components and Interfaces

### User Model
```typescript
interface User {
  id: string;
  email: string;
  passwordHash: string;
  createdAt: Date;
  lastLoginAt?: Date;
}
```

### Authentication Service
```typescript
interface AuthService {
  register(email: string, password: string): Promise<User>;
  login(email: string, password: string): Promise<{ user: User; token: string }>;
  validateToken(token: string): Promise<User>;
  logout(token: string): Promise<void>;
}
```

## Data Models
- **User Storage**: Database table with indexed email field
- **Session Management**: JWT tokens with expiration
- **Rate Limiting**: In-memory or Redis-based attempt tracking

## Error Handling
- Input validation errors (400 Bad Request)
- Authentication failures (401 Unauthorized)
- Rate limiting (429 Too Many Requests)
- Server errors (500 Internal Server Error)

## Testing Strategy
- Unit tests for each service component
- Integration tests for complete authentication flows
- Security testing for common vulnerabilities
- Performance testing for concurrent authentication requests
```

#### Tasks Document

```markdown
# User Authentication System - Implementation Plan

- [ ] 1. Set up project structure and dependencies
  - Create authentication module directory structure
  - Install required dependencies (bcrypt, jsonwebtoken, validation library)
  - Set up TypeScript interfaces and types
  - _Requirements: 1.1, 2.1, 3.1_

- [ ] 2. Implement User model and validation
  - Create User interface and data model
  - Implement email validation with regex patterns
  - Create password strength validation (min length, complexity)
  - Write unit tests for validation functions
  - _Requirements: 1.1, 1.3, 1.4_

- [ ] 3. Create password security service
  - Implement password hashing using bcrypt with appropriate salt rounds
  - Create password comparison function for login validation
  - Write unit tests for password hashing and verification
  - _Requirements: 1.1, 2.1_

- [ ] 4. Build JWT token service
  - Implement token generation with user payload and expiration
  - Create token validation and decoding functionality
  - Add token blacklisting capability for logout
  - Write unit tests for token operations
  - _Requirements: 2.1, 3.1, 3.2, 3.4_

- [ ] 5. Implement user repository layer
  - Create database schema and migration for users table
  - Implement CRUD operations for user management
  - Add email uniqueness constraint and indexing
  - Write integration tests for database operations
  - _Requirements: 1.1, 1.2, 2.1_

- [ ] 6. Build authentication service
  - Implement user registration with validation and password hashing
  - Create login functionality with credential verification
  - Add session validation for protected routes
  - Implement logout with token invalidation
  - Write comprehensive unit tests for all authentication flows
  - _Requirements: 1.1, 1.2, 2.1, 2.2, 3.1, 3.4_

- [ ] 7. Create authentication middleware
  - Build middleware to validate JWT tokens on protected routes
  - Implement proper error handling and response formatting
  - Add request context with authenticated user information
  - Write integration tests for middleware functionality
  - _Requirements: 3.1, 3.2_

- [ ] 8. Implement rate limiting and security measures
  - Add rate limiting for login attempts per IP/email
  - Implement account lockout after multiple failed attempts
  - Create security headers and CORS configuration
  - Write tests for security measures
  - _Requirements: 2.3_

- [ ] 9. Create API endpoints and integration
  - Build REST endpoints for registration, login, and logout
  - Implement proper HTTP status codes and error responses
  - Add request/response validation and sanitization
  - Write end-to-end API tests
  - _Requirements: 1.1, 2.1, 3.4_

- [ ] 10. Add monitoring and logging
  - Implement structured logging for authentication events
  - Add metrics for login success/failure rates
  - Create alerts for suspicious authentication patterns
  - Write tests for logging functionality
  - _Requirements: 2.3_
```

### Key Decision Commentary

#### Why JWT Tokens?
- **Stateless**: No server-side session storage required
- **Scalable**: Works well in distributed systems
- **Standard**: Well-established security practices
- **Flexible**: Can include custom claims and expiration

#### Why bcrypt for Password Hashing?
- **Adaptive**: Can increase difficulty as hardware improves
- **Salt Built-in**: Automatic salt generation prevents rainbow table attacks
- **Battle-tested**: Widely used and audited security library
- **Configurable**: Adjustable work factor for security/performance balance

#### Database Design Decisions
- **Email as Username**: Simpler for users, natural unique identifier
- **Separate Password Hash**: Never store plain text passwords
- **Timestamps**: Track account creation and last login for analytics
- **Indexing**: Email field indexed for fast lookup during login

### Implementation Notes

This spec translates to approximately 8-10 TypeScript files:
- `models/User.ts` - Data model and interfaces
- `services/AuthService.ts` - Core authentication logic
- `services/PasswordService.ts` - Password hashing utilities
- `services/TokenService.ts` - JWT token management
- `repositories/UserRepository.ts` - Database operations
- `middleware/AuthMiddleware.ts` - Request authentication
- `controllers/AuthController.ts` - HTTP endpoint handlers
- `routes/auth.ts` - Route definitions
- `__tests__/` - Comprehensive test suite

### Lessons Learned

**What Worked Well:**
- Breaking down authentication into discrete services made testing easier
- Starting with clear interfaces helped maintain consistency
- Security considerations were addressed systematically

**What Could Be Improved:**
- Could have included more specific error message requirements
- Rate limiting strategy could be more detailed in design phase
- Password reset functionality was not included but often needed

---

## Example 2: Data Validation Component

### Overview
A reusable data validation component that can validate different types of input data with customizable rules. This example shows how to spec a utility component that will be used across multiple features.

### Complete Spec Documents

#### Requirements Document

```markdown
# Data Validation Component - Requirements

## Introduction
This feature implements a flexible data validation component that can validate various types of input data against configurable rules. The component will support common validation patterns, custom validation functions, and provide clear error messaging for failed validations.

## Requirements

### Requirement 1
**User Story:** As a developer, I want a validation component that can validate common data types, so that I can ensure data integrity across my application.

#### Acceptance Criteria
1. WHEN validating string data THEN the system SHALL support length, pattern, and format validations
2. WHEN validating numeric data THEN the system SHALL support range, precision, and type validations
3. WHEN validating email addresses THEN the system SHALL use standard email format validation
4. WHEN validating dates THEN the system SHALL support format and range validations

### Requirement 2
**User Story:** As a developer, I want to define custom validation rules, so that I can validate domain-specific data requirements.

#### Acceptance Criteria
1. WHEN defining custom validators THEN the system SHALL accept custom validation functions
2. WHEN combining multiple validators THEN the system SHALL support validation chains
3. WHEN validation fails THEN the system SHALL provide specific error messages
4. IF validation passes THEN the system SHALL return the validated data

### Requirement 3
**User Story:** As a developer, I want clear validation error messages, so that I can provide meaningful feedback to users.

#### Acceptance Criteria
1. WHEN validation fails THEN the system SHALL return descriptive error messages
2. WHEN multiple validations fail THEN the system SHALL collect all error messages
3. WHEN displaying errors THEN the system SHALL identify which field failed validation
4. IF custom error messages are provided THEN the system SHALL use them instead of defaults
```

#### Design Document

```markdown
# Data Validation Component - Design

## Overview
The validation component will be implemented as a composable validation system that supports both built-in validators and custom validation functions. It will use a fluent API for chaining validators and provide detailed error reporting.

## Architecture
- **Validator Interface**: Common interface for all validation functions
- **Built-in Validators**: Pre-defined validators for common use cases
- **Validation Chain**: Composable validation pipeline
- **Error Collector**: Aggregates and formats validation errors
- **Schema Validator**: Validates complex objects with multiple fields

## Components and Interfaces

### Core Validator Interface
```typescript
interface Validator<T> {
  validate(value: T): ValidationResult;
  withMessage(message: string): Validator<T>;
}

interface ValidationResult {
  isValid: boolean;
  errors: string[];
  value?: any;
}
```

### Validation Chain
```typescript
interface ValidationChain<T> {
  required(): ValidationChain<T>;
  string(): ValidationChain<string>;
  number(): ValidationChain<number>;
  email(): ValidationChain<string>;
  minLength(min: number): ValidationChain<string>;
  maxLength(max: number): ValidationChain<string>;
  pattern(regex: RegExp): ValidationChain<string>;
  custom(validator: (value: T) => boolean): ValidationChain<T>;
  validate(value: T): ValidationResult;
}
```

## Data Models
- **Validation Rules**: Configuration objects for different validation types
- **Error Messages**: Localized error message templates
- **Schema Definitions**: Object validation schemas with field-level rules

## Error Handling
- Validation errors collected and formatted consistently
- Support for custom error messages and internationalization
- Field-level error mapping for form validation
- Graceful handling of invalid input types

## Testing Strategy
- Unit tests for each built-in validator
- Integration tests for validation chains
- Edge case testing for boundary conditions
- Performance testing for large data sets
```

#### Tasks Document

```markdown
# Data Validation Component - Implementation Plan

- [ ] 1. Set up validation component structure
  - Create validation module directory and core interfaces
  - Define TypeScript types for validators and results
  - Set up testing framework and initial test structure
  - _Requirements: 1.1, 2.1, 3.1_

- [ ] 2. Implement core validation interfaces
  - Create base Validator interface and ValidationResult type
  - Implement ValidationChain class with fluent API
  - Create error collection and formatting utilities
  - Write unit tests for core interfaces
  - _Requirements: 2.1, 2.2, 3.1, 3.2_

- [ ] 3. Build built-in string validators
  - Implement required, minLength, maxLength validators
  - Create pattern matching validator with regex support
  - Add email format validation with comprehensive regex
  - Write unit tests for all string validators
  - _Requirements: 1.1, 1.3_

- [ ] 4. Create numeric validators
  - Implement number type validation and conversion
  - Add min, max, and range validation functions
  - Create precision and decimal place validators
  - Write unit tests for numeric validation edge cases
  - _Requirements: 1.2_

- [ ] 5. Implement date and time validators
  - Create date format validation and parsing
  - Add date range validators (before, after, between)
  - Implement time format validation
  - Write unit tests for various date formats and edge cases
  - _Requirements: 1.4_

- [ ] 6. Build custom validation support
  - Implement custom validator function interface
  - Create validation chain composition for multiple validators
  - Add conditional validation support
  - Write unit tests for custom validator integration
  - _Requirements: 2.1, 2.2_

- [ ] 7. Create error message system
  - Implement default error message templates
  - Add support for custom error messages per validator
  - Create error message interpolation for dynamic values
  - Write tests for error message generation and formatting
  - _Requirements: 3.1, 3.2, 3.4_

- [ ] 8. Build object schema validation
  - Create schema definition interface for complex objects
  - Implement field-level validation with error mapping
  - Add nested object validation support
  - Write integration tests for complete object validation
  - _Requirements: 2.2, 3.3_

- [ ] 9. Add validation utilities and helpers
  - Create validation result aggregation utilities
  - Implement validation middleware for common frameworks
  - Add form validation helpers and integration examples
  - Write comprehensive integration tests
  - _Requirements: 2.2, 3.3_

- [ ] 10. Performance optimization and finalization
  - Optimize validation chains for performance
  - Add caching for compiled regex patterns
  - Create comprehensive documentation and usage examples
  - Write performance tests and benchmarks
  - _Requirements: 1.1, 2.1, 3.1_
```

### Key Decision Commentary

#### Why Fluent API Design?
- **Developer Experience**: Intuitive chaining syntax
- **Composability**: Easy to combine multiple validators
- **Readability**: Validation rules read like natural language
- **Flexibility**: Can add new validators without breaking existing code

#### Error Collection Strategy
- **Comprehensive**: Collect all validation errors, not just the first
- **Structured**: Consistent error format across all validators
- **Customizable**: Allow custom error messages for better UX
- **Localizable**: Support for internationalization

### Implementation Notes

This spec results in a modular validation library:
- `core/Validator.ts` - Base interfaces and types
- `core/ValidationChain.ts` - Fluent API implementation
- `validators/StringValidators.ts` - String validation functions
- `validators/NumberValidators.ts` - Numeric validation functions
- `validators/DateValidators.ts` - Date/time validation functions
- `utils/ErrorCollector.ts` - Error aggregation utilities
- `schema/ObjectValidator.ts` - Complex object validation
- `__tests__/` - Comprehensive test coverage

### Lessons Learned

**What Worked Well:**
- Fluent API made the component very developer-friendly
- Separating built-in and custom validators provided good flexibility
- Comprehensive error collection improved debugging experience

**What Could Be Improved:**
- Could have specified performance requirements more clearly
- Async validation support wasn't considered but might be needed
- Integration with popular form libraries could be more detailed

---

## Usage Guidelines

### When to Use These Examples

**User Authentication Example** is ideal for:
- Learning how to spec security-critical features
- Understanding how to break down complex business logic
- Seeing how security requirements translate to implementation tasks

**Data Validation Example** is perfect for:
- Understanding utility component specification
- Learning how to design reusable, composable systems
- Seeing how developer experience requirements drive design decisions

### Adapting These Examples

Both examples can be adapted for different contexts:
- **Technology Stack**: Replace specific technologies while keeping the structure
- **Complexity Level**: Add or remove features based on project needs
- **Domain Requirements**: Modify business rules while maintaining the process
- **Integration Needs**: Adjust interfaces based on existing system architecture

---

[← Back to Examples Overview](README.md) | [Complex System Examples →](complex-system-spec.md)