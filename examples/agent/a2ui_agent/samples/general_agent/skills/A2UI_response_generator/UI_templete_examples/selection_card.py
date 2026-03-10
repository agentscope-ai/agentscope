# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""A2UI template example for selection card."""

SELECTION_CARD_EXAMPLE = """
---BEGIN SELECTION_CARD_EXAMPLE---
Use this template to display a single-choice question card using MultipleChoice component.

[
  { "beginRendering": { "surfaceId": "quiz-card", "root": "quiz-card-root", "styles": { "primaryColor": "#673AB7", "font": "Roboto" } } },
  { "surfaceUpdate": {
    "surfaceId": "quiz-card",
    "components": [
      { "id": "quiz-card-root", "component": { "Card": { "child": "quiz-card-content" } } },
      { "id": "quiz-card-content", "component": { "Column": { "children": { "explicitList": ["quiz-title", "quiz-question", "quiz-multiple-choice", "quiz-submit-button"] } } } },
      { "id": "quiz-title", "component": { "Text": { "usageHint": "h1", "text": { "path": "/title" } } } },
      { "id": "quiz-question", "component": { "Text": { "usageHint": "h2", "text": { "path": "/question" } } } },
      { "id": "quiz-multiple-choice", "component": { "MultipleChoice": { "selections": { "path": "/selectedOptions" }, "options": [
        { "label": { "literalString": "[Option A]" }, "value": "A" },
        { "label": { "literalString": "[Option B]" }, "value": "B" },
        { "label": { "literalString": "[Option C]" }, "value": "C" },
        { "label": { "literalString": "[Option D]" }, "value": "D" }
      ], "maxAllowedSelections": 1 } } },
      { "id": "quiz-submit-button", "component": { "Button": { "child": "submit-text", "primary": true, "action": { "name": "submit_answer", "context": [ { "key": "questionId", "value": { "path": "/questionId" } }, { "key": "selectedOptions", "value": { "path": "/selectedOptions" } } ] } } } },
      { "id": "submit-text", "component": { "Text": { "text": { "literalString": "Submit Answer" } } } }
    ]
  } },
  { "dataModelUpdate": {
    "surfaceId": "quiz-card",
    "path": "/",
    "contents": [
      { "key": "title", "valueString": "[Quiz Title]" },
      { "key": "question", "valueString": "[Question Content]" },
      { "key": "questionId", "valueString": "[Question ID]" },
      { "key": "selectedOptions", "valueMap": [] }
    ]
  } }
]
---END SELECTION_CARD_EXAMPLE---
"""


MULTIPLE_SELECTION_CARDS_EXAMPLE = """
---BEGIN MULTIPLE_SELECTION_CARDS_EXAMPLE---
Use this template to display multiple selection cards in a vertical list. Each card represents a separate question with checkboxes for each option.

[
  {
    "beginRendering": {
      "surfaceId": "multi-quiz",
      "root": "root-column",
      "styles": {
        "primaryColor": "#673AB7",
        "font": "Roboto"
      }
    }
  },
  {
    "surfaceUpdate": {
      "surfaceId": "multi-quiz",
      "components": [
        {
          "id": "root-column",
          "component": {
            "Column": {
              "children": {
                "explicitList": ["page-title", "quiz-list"]
              }
            }
          }
        },
        {
          "id": "page-title",
          "component": {
            "Text": {
              "usageHint": "h1",
              "text": {
                "path": "pageTitle"
              }
            }
          }
        },
        {
          "id": "quiz-list",
          "component": {
            "List": {
              "direction": "vertical",
              "children": {
                "template": {
                  "componentId": "quiz-card-template",
                  "dataBinding": "/questions"
                }
              }
            }
          }
        },
        {
          "id": "quiz-card-template",
          "component": {
            "Card": {
              "child": "quiz-card-content"
            }
          }
        },
        {
          "id": "quiz-card-content",
          "component": {
            "Column": {
              "children": {
                "explicitList": ["quiz-title", "quiz-question", "quiz-multiple-choice", "quiz-submit-button"]
              }
            }
          }
        },
        {
          "id": "quiz-title",
          "component": {
            "Text": {
              "usageHint": "h2",
              "text": {
                "path": "title"
              }
            }
          }
        },
        {
          "id": "quiz-question",
          "component": {
            "Text": {
              "usageHint": "h3",
              "text": {
                "path": "question"
              }
            }
          }
        },
        {
          "id": "quiz-multiple-choice",
          "component": {
            "MultipleChoice": {
              "selections": {
                "path": "selectedOptions"
              },
              "options": {
                "template": {
                  "componentId": "option-template",
                  "dataBinding": "options"
                }
              },
              "maxAllowedSelections": 3
            }
          }
        },
        {
          "id": "option-template",
          "component": {
            "MultipleChoiceOption": {
              "label": {
                "path": "text"
              },
              "value": {
                "path": "id"
              }
            }
          }
        },
        {
          "id": "quiz-submit-button",
          "component": {
            "Button": {
              "child": "submit-text",
              "primary": true,
              "action": {
                "name": "submit_answer",
                "context": [
                  {
                    "key": "questionId",
                    "value": {
                      "path": "questionId"
                    }
                  },
                  {
                    "key": "questionTitle",
                    "value": {
                      "path": "title"
                    }
                  },
                  {
                    "key": "selectedOptions",
                    "value": {
                      "path": "selectedOptions"
                    }
                  }
                ]
              }
            }
          }
        },
        {
          "id": "submit-text",
          "component": {
            "Text": {
              "text": {
                "literalString": "Submit Answer"
              }
            }
          }
        }
      ]
    }
  },
  {
    "dataModelUpdate": {
      "surfaceId": "multi-quiz",
      "path": "/",
      "contents": [
        {
          "key": "pageTitle",
          "valueString": "[Page Title]"
        },
        {
          "key": "questions",
          "valueMap": [
            {
              "key": "question1",
              "valueMap": [
                {
                  "key": "questionId",
                  "valueString": "q1"
                },
                {
                  "key": "title",
                  "valueString": "[Question 1 Title]"
                },
                {
                  "key": "question",
                  "valueString": "[Question 1 Content]"
                },
                {
                  "key": "selectedOptions",
                  "valueList": []
                },
                {
                  "key": "options",
                  "valueMap": [
                    {
                      "key": "option1",
                      "valueMap": [
                        {
                          "key": "id",
                          "valueString": "A"
                        },
                        {
                          "key": "text",
                          "valueString": "[Option A]"
                        }
                      ]
                    },
                    {
                      "key": "option2",
                      "valueMap": [
                        {
                          "key": "id",
                          "valueString": "B"
                        },
                        {
                          "key": "text",
                          "valueString": "[Option B]"
                        }
                      ]
                    },
                    {
                      "key": "option3",
                      "valueMap": [
                        {
                          "key": "id",
                          "valueString": "C"
                        },
                        {
                          "key": "text",
                          "valueString": "[Option C]"
                        }
                      ]
                    }
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  }
]


---END MULTIPLE_SELECTION_CARDS_EXAMPLE---
"""
