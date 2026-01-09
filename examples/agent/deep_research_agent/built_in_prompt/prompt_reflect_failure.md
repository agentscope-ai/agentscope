Your job is reflecting your failure based on your work history and generate the follow-up subtask. You have already found that one of the subtask in the Working Plan cannot be succesfully completed according to your work history.

## Instructions
1. Examine the Work History to precisely pinpoint the failed subtask in Working Plan.
2. Review the Current Subtask and Task Final Objective provided in Work History, carefully analyze whether this subtask was designed incorrectly due to a misunderstanding of the task. If so,
    * set `need_rephrase` in `rephrase_subtask` as true
    * Only replace the inappropriate subtask with modified subtask, while preserving the rest of the Working Plan remain unchanged. You should output the updated Working Plan in `rephrased_plan`.
    * If the subtask was not poorly designed, proceed to Step 3.
3. Carefully retrieve the previous subtask objective in Work History to check for any signs of getting stuck in **repetitive patterns** in generating similar subtask.
    * If so, avoid unnecessary decomposition by setting `need_decompose` in `decompose_subtask` as false.
    * Otherwise, set `need_decompose` as true and only output the failed subtask without any additional reasoning in `failed_subtask`.

## Important Notes
1. `need_decompose` and `need_rephrase` can NOT be both true at the same time.
2. Set `need_decompose` and `need_rephrase` as false simultaneously when you find that you are getting stuck in a repetitive failure pattern.

## Example
Work History:
1. Reflect the failure of this subtask and identify the failed subtask "Convert the extracted geographic coordinates or landmarks into corresponding five-digit zip codes by mapping tools or geo-mapping APIs"
2. Decompose subtask "Convert the extracted geographic coordinates or landmarks into corresponding five-digit zip codes by mapping tools or geo-mapping APIs" and generate a plan.
Working Plan:
1. Extract detailed geographic data  focusing on Fred Howard Park and associated HUC code.
2. Use mapping tools or geo-mapping APIs (e.g., 'maps_regeocode') to convert the extracted geographic coordinates or landmarks into corresponding five-digit zip codes.
3. Verify the accuracy of the generated zip codes by cross-referencing them with external databases or additional resources to ensure inclusion of all Clownfish occurrence locations.
4. Compile the verified zip codes into a formatted list as required by the user, ensuring clarity and adherence to specifications.
Failed Subtask: "Use mapping tools or geo-mapping APIs (e.g., 'maps_regeocode') to convert the extracted geographic coordinates or landmarks into corresponding five-digit zip codes."
Output:
```json
{
    "rephrase_subtask":{
        "need_rephrase": false,
        "rephrased_plan": ""
    },
    "decompose_subtask":{
        "need_decompose": false,
        "failed_subtask": ""
    }
}
```
Explanation: The current failed subtask "Use mapping tools or geo-mapping APIs (e.g., 'maps_regeocode') to convert the extracted geographic coordinates or landmarks into corresponding five-digit zip codes" is similar to the previous failed subtask "Convert the extracted geographic coordinates or landmarks into corresponding five-digit zip codes by mapping tools or geo-mapping APIs", which has already been identified and decomposed in work history. Therefore, we don't need to make decomposition repeatedly.

### Output Format Requirements
* Ensure proper JSON formatting with escaped special characters where needed.
* Line breaks within text fields should be represented as `\n` in the JSON output.
* There is no specific limit on field lengths, but aim for concise descriptions.
* All field values must be strings.
* For each JSON document, only include the following fields: