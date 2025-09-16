# Micro Spec Template

<!-- Navigation Metadata -->
<!-- Section: Templates | Level: Practical | Prerequisites: ../methodology/lightweight-specs.md -->
<!-- Related: quick-spec-template.md, checklists.md -->

**📍 You are here:** [Main Guide](../../README.md) → [Templates](README.md) → **Micro Spec Template**

## Quick Navigation
- **Prerequisites:** [Lightweight Specs](../methodology/lightweight-specs.md)
- **Related:** [Quick Spec Template](quick-spec-template.md), [Checklists](checklists.md)

---

Ultra-lightweight template for changes under 1 day of effort - bug fixes, copy changes, minor tweaks.

## Template Usage

**Copy the template below and fill in the bracketed sections. This should take less than 5 minutes to complete.**

---

# [Brief Change Description]

**Type:** [Bug Fix/Copy Change/Config Update/Minor Feature]  
**Effort:** [X hours]  
**Assignee:** [Name]  
**Date:** [YYYY-MM-DD]

## What
[One sentence describing the change]

## Why  
[Brief justification - why is this needed?]

## How
- [Implementation step 1]
- [Implementation step 2]  
- [Implementation step 3]

## Acceptance
[Simple pass/fail criteria - how do you know it worked?]

## Files
- `[file/path]` - [what changes]

---

## Examples

### Bug Fix Example
# Fix Login Button Alignment

**Type:** Bug Fix  
**Effort:** 2 hours  
**Assignee:** Alex Kim  
**Date:** 2024-01-15

## What
Fix login button that's misaligned on mobile devices

## Why  
Button is partially cut off on screens smaller than 375px, preventing users from logging in

## How
- Update CSS media query in `login.css` to use `flex-direction: column` for small screens
- Adjust button margin from `10px` to `5px` for mobile
- Test on iPhone SE and Android small screens

## Acceptance
Login button is fully visible and clickable on all screen sizes 375px and above

## Files
- `styles/login.css` - Update mobile media query

---

### Copy Change Example
# Update Welcome Message

**Type:** Copy Change  
**Effort:** 30 minutes  
**Assignee:** Maria Santos  
**Date:** 2024-01-15

## What
Change homepage welcome message to reflect new product positioning

## Why  
Marketing team updated messaging to emphasize "collaboration" over "productivity"

## How
- Replace "Boost your productivity" with "Enhance team collaboration"
- Update subheading from "Get more done" to "Work better together"
- Verify text fits in existing design layout

## Acceptance
Homepage displays new messaging and layout looks correct on desktop and mobile

## Files
- `components/HomePage.jsx` - Update welcome text constants

---

### Configuration Update Example
# Enable Feature Flag for Premium Users

**Type:** Config Update  
**Effort:** 1 hour  
**Assignee:** David Chen  
**Date:** 2024-01-15

## What
Enable `advanced_analytics` feature flag for users with premium subscriptions

## Why  
Premium feature is ready for release to paying customers only

## How
- Update feature flag config to check user subscription status
- Set flag to `true` for premium users, `false` for free users
- Deploy config change to production

## Acceptance
Premium users see advanced analytics dashboard, free users do not

## Files
- `config/feature-flags.json` - Update advanced_analytics rule

---

### Minor UI Tweak Example
# Add Loading Spinner to Save Button

**Type:** Minor Feature  
**Effort:** 3 hours  
**Assignee:** Lisa Park  
**Date:** 2024-01-15

## What
Show loading spinner on save button while form is submitting

## Why  
Users are clicking save multiple times because they don't know if it's working

## How
- Add loading state to save button component
- Show spinner icon and disable button during API call
- Reset state when save completes or fails

## Acceptance
Save button shows spinner and is disabled during form submission, returns to normal when complete

## Files
- `components/SaveButton.jsx` - Add loading state logic

---

## Quick Checklists

### Before Starting
- [ ] Change is clearly defined
- [ ] Effort estimate is under 1 day
- [ ] No dependencies on other work
- [ ] Success criteria are obvious

### During Implementation  
- [ ] Make minimal necessary changes
- [ ] Test the specific change
- [ ] Verify no unintended side effects

### Before Completing
- [ ] Acceptance criteria met
- [ ] Code reviewed (if required)
- [ ] Change deployed/merged
- [ ] Stakeholder notified (if needed)

## When to Upgrade

**Upgrade to Quick Spec if:**
- Implementation takes longer than expected
- Multiple components need changes
- Other developers need clarification
- Testing reveals additional requirements

**Upgrade to Standard Spec if:**
- Design questions arise
- Dependencies on other teams discovered
- Performance or security implications found
- Stakeholder review is needed

## Common Patterns

### CSS/Style Changes
```markdown
# [Style Change Description]

**What:** [Visual change needed]
**Why:** [Design/UX reason]
**How:** 
- Update CSS property `[property]` from `[old]` to `[new]`
- Test on [browsers/devices]
**Acceptance:** [Visual result description]
**Files:** `[css-file]` - [specific changes]
```

### Text/Copy Updates
```markdown
# [Copy Change Description]

**What:** [Text that needs updating]
**Why:** [Business/marketing reason]
**How:**
- Replace "[old text]" with "[new text]"
- Verify text fits in UI layout
**Acceptance:** [New text displays correctly]
**Files:** `[component-file]` - [text constants]
```

### Configuration Changes
```markdown
# [Config Change Description]

**What:** [Setting that needs updating]
**Why:** [Operational reason]
**How:**
- Update config value `[key]` from `[old]` to `[new]`
- Deploy to [environment]
**Acceptance:** [System behavior change]
**Files:** `[config-file]` - [specific setting]
```

### Simple Bug Fixes
```markdown
# Bug: [Issue Description]

**What:** [What's broken]
**Why:** [Root cause]
**How:**
- [Fix step 1]
- [Fix step 2]
**Acceptance:** [Issue no longer occurs]
**Files:** `[file]` - [fix description]
```

---

## 🔗 Related Content

### Prerequisites
- [Lightweight Specs Guide](../methodology/lightweight-specs.md)

### Other Templates
- [Quick Spec Template](quick-spec-template.md) - For 1-3 day features
- [Checklists](checklists.md) - Quality gates and reviews

### Examples
- [Micro Spec Examples](../examples/micro-spec-examples.md)

[← Back to Templates](README.md) | [Quick Spec Template →](quick-spec-template.md)