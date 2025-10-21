Module: `src/agentscope/evaluate/_ace_benchmark/_ace_tools_api/`
Responsibility: ACE benchmark tools API simulation and integration
Key Types: `FoodPlatformAPI`, `MessageAPI`, `ReminderAPI`, `TravelAPI`

Key Functions/Methods
- `FoodPlatformAPI.get_food_price(location_id, food_id) → dict`
  - Purpose: Simulate food platform API for benchmark testing
  - Inputs: Location identifier, food item identifier
  - Returns: Price information, availability status
  - Side-effects: File I/O for mock data persistence
  - References: `src/agentscope/evaluate/_ace_benchmark/_ace_tools_api/_food_platform_api.py:45`
  - Type Safety: Strict typing for API contracts

- `MessageAPI.send_message(recipient_id, message, urgency_level="normal") → dict`
  - Purpose: Message delivery simulation with urgency levels
  - Inputs: Recipient identifier, message content, urgency level
  - Returns: Delivery status, timestamp, confirmation ID

- `ReminderAPI.create_reminder(user_id, reminder_time, message) → dict`
  - Purpose: Create reminder notifications with timing
  - Side-effects: Updates reminder queue and scheduling

- `TravelAPI.search_travel_options(departure_city, destination_city, travel_date) → list[dict]`
  - Purpose: Travel option search and comparison

Call Graph
- `AceEvaluator._run_agent_phase` → API tool calls → simulated response generation

## Testing Strategy
- Unit tests: Benchmark simulation scenarios
- Integration: Full ACE benchmark workflow testing
- Edge cases: API rate limiting, network failures, malformed responses
- Type coverage: 100% parameter typing with runtime validation

## Related SOP: `docs/evaluate/SOP.md`