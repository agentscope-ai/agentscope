# Structured Output Example

## What This Example Demonstrates

This example showcases **structured output generation** using AgentScope with Pydantic models. It demonstrates how to constrain AI model outputs to follow specific data structures and formats, ensuring consistent and parseable responses.

### Key Features:
- **Structured Data Generation**: Forces agent responses to conform to
  predefined schemas
- **Pydantic Integration**: Uses Pydantic models to define output structure with validation
- **Type Safety**: Ensures output data types match expected formats
- **Field Validation**: Includes constraints like age limits (0-120) and enum choices
- **JSON Output**: Generates clean, structured JSON responses

### Example Models:

1. **TableModel**: Structured person information
   - `name`: Person's name (string)
   - `age`: Person's age (integer,0-120)
   - `intro`: One-sentence introduction (string)
   - `honors`: List of honors/achievements (array of strings)

2. **ChoiceModel**: Constrained choice selection
   - `choice`: Must be one of "apple", "banana", or "orange"

### Use Cases:
- **Data Extraction**: Extract structured information from unstructured text
- **Form Generation**: Generate consistent data for databases or APIs
- **Survey Responses**: Ensure responses fit predefined categories
- **Content Classification**: Categorize content into specific types

## How to Run This Example
1. **Set Environment Variable:**
   ```bash
   export DASHSCOPE_API_KEY="your_dashscope_api_key_here"
   ```
2. **Run the script:**
    ```bash
   python main.py
   ```
3. **Expected Output:**
The program will generate two structured responses like below:
```
Structured Output 1:
{
    "name": "Albert Einstein",
    "age": 76,
    "intro": 1,
    "honors": [
        "Nobel Prize in Physics (1921)",
        "Copley Medal (1925)"
    ]
}
Structured Output 2:
{
    "choice": "apple"
}
```

>💡**Note:** The specific content will vary with each run since the agent generates different responses, but the JSON structure will always conform to the predefined Pydantic models (`TableModel` and `ChoiceModel`).

## How It Works:
1. The agent receives a query along with a structured_model parameter
2. The agent generates a response that conforms to the Pydantic model schema
3. The structured data is returned in res.metadata as a validated JSON object
4. Pydantic ensures all field types and constraints are satisfied

## Custom Pydantic Models
Create your own structured output models for specific use cases, for example:

```
from typing import Optional
from pydantic import BaseModel, Field, EmailStr

class BusinessModel(BaseModel):
    """Business information extraction model."""

    company_name: str = Field(description="Name of the company")
    industry: str = Field(description="Industry sector")
    founded_year: int = Field(description="Year founded", ge=1800, le=2024)
    headquarters: str = Field(description="Location of headquarters")
    employee_count: Optional[int] = Field(description="Number of employees", ge=1)
    email: Optional[EmailStr] = Field(description="Contact email address")
    website: Optional[str] = Field(description="Company website URL")

# Usage
query = Msg("user", "Tell me about Tesla Inc.", "user")
res = await agent(query, structured_model=BusinessModel)
```

## Best Practices for Structured Output

1. **Use Descriptive Field Names:** Make field purposes clear
2. **Add Field Descriptions:** Help the agent understand what data to generate
3. **Set Validation Constraints:** Use Pydantic validators for data integrity
4. **Choose Appropriate Types:** Use specific types like EmailStr, datetime, etc.