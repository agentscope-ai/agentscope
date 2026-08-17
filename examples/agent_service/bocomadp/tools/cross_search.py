# -*- coding: utf-8 -*-
"""跨知识搜索工具。

跨场景 / 团队 / 个人知识库进行混合召回搜索，支持全文检索和向量检索。

【迁移说明】
本模块由 ``deerflow`` 的 ``cross_search_tool`` 迁移而来，做了适配：
    - 去掉 ``langchain.tools.tool`` 装饰器：AgentScope 的 ``FunctionTool``
      会自动从函数签名 / docstring 提取工具名、描述与参数 schema；
    - 配置收拢到 ``bocomadp/config/cross_search_config.py`` 模块
      （从 ``config.yaml`` 的 ``cross_search`` 节点提取，字符串值支持
      ``$VAR`` 环境变量引用展开），本模块仅负责使用配置，不再自解析环境变量；
    - 用 ``httpx`` 替代 ``requests``：``httpx`` 是本项目已有依赖，且支持
      multipart ``files`` 上传，接口基本对齐。

接入真实环境时，只需配置 ``config.yaml`` 的 ``cross_search.api_url``、
``caller`` / ``user_code``（见 ``config.yaml.example`` 与 ``.env.example``）。
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

try:
    from agentscope.tool import FunctionTool, ToolMiddlewareBase
except ImportError:
    FunctionTool = ToolMiddlewareBase = None

from ..config.cross_search_config import CrossSearchConfig, get_cross_search_config
from ..deerflow.custom_params import get_custom_params

logger = logging.getLogger(__name__)

# 请求级 custom_params 中允许强制覆盖本工具参数的 key 集合
# （与工具函数签名参数同名，下划线形式）。
_OVERRIDE_KEYS = (
    "space_code_list",
    "team_space_code_list",
    "psnl_space_code_id",
    "user_code",
    "search_type",
    "customized_tag_list",
    "psnl_category_id_list",
)


class _SpacecodeOverrideMiddleware(ToolMiddlewareBase):
    """空间码参数强制覆盖中间件（对齐 deer-flow SpacecodeOverrideMiddleware）。

    每次工具调用时读取请求级 custom_params（ContextVar，由 deerflow
    路由层在 spawn run 前注入），对 :data:`_OVERRIDE_KEYS` 中的参数
    直接赋值覆盖——即使模型传错值也会被纠正为请求方指定的空间码。

    ``personal_search_switch`` 显式 ``False`` 时，在覆盖之后清空
    ``psnl_space_code_id`` / ``psnl_category_id_list``（对齐 deer-flow：
    该开关为 False 时不挂个人知识库搜索工具；bocomadp 无独立个人
    搜索工具，等价于禁用 cross_search 的个人检索维度）。config 中
    对应默认值为空，参数置 ``None`` 后不会回填。
    """

    async def on_tool_call(self, tool, input_kwargs, next_handler):
        params = get_custom_params()
        for key in _OVERRIDE_KEYS:
            value = params.get(key)
            if value is not None:
                previous = input_kwargs.get(key)
                input_kwargs[key] = value  # 强制覆盖，模型传错的也纠正
                logger.info(
                    "SpacecodeOverride: %s %r -> %r",
                    key,
                    previous,
                    value,
                )
        # 个人检索显式关闭优先于空间码覆盖（关闭开关后置处理）
        if params.get("personal_search_switch") is False:
            for key in ("psnl_space_code_id", "psnl_category_id_list"):
                previous = input_kwargs.get(key)
                if previous:
                    input_kwargs[key] = None
                    logger.info(
                        "SpacecodeOverride: personal_search_switch=false, "
                        "%s %r -> None",
                        key,
                        previous,
                    )
        async for chunk in next_handler(**input_kwargs):
            yield chunk



def _build_req_message(
    keyword: str,
    config: CrossSearchConfig,
    *,
    search_type: str | None = None,
    user_code: str | None = None,
    space_code_list: list[str] | None = None,
    team_space_code_list: list[str] | None = None,
    psnl_space_code_id: str | None = None,
    customized_tag_list: list[str] | None = None,
    psnl_category_id_list: list[str] | None = None,
    text_top_n: int | None = None,
    vector_top_n: int | None = None,
) -> str:
    effective_user_code = user_code or config.user_code
    if not effective_user_code:
        raise ValueError(
            "userCode is required. 请在 config.yaml 的 cross_search.user_code 中配置。",
        )

    effective_search_type = search_type or config.search_type
    effective_space_code_list = (
        space_code_list
        if space_code_list is not None
        else config.space_code_list
    )
    effective_team_space_code_list = (
        team_space_code_list
        if team_space_code_list is not None
        else config.team_space_code_list
    )
    effective_psnl_space_code_id = (
        psnl_space_code_id or config.psnl_space_code_id
    )
    effective_customized_tag_list = (
        customized_tag_list
        if customized_tag_list is not None
        else config.customized_tag_list
    )
    effective_psnl_category_id_list = (
        psnl_category_id_list
        if psnl_category_id_list is not None
        else config.psnl_category_id_list
    )

    has_space = bool(
        effective_space_code_list
        or effective_team_space_code_list
        or effective_psnl_space_code_id
    )
    if not has_space:
        raise ValueError(
            "至少需要提供 spaceCodeList、teamSpaceCodeList 或 "
            "psnlSpaceCodeId 中的一个。",
        )

    param: dict[str, Any] = {
        "keyword": keyword,
        "userCode": effective_user_code,
        "userRole": config.user_role,
        "searchType": effective_search_type,
        "textTopN": (
            text_top_n if text_top_n is not None else config.text_top_n
        ),
        "vectorTopN": (
            vector_top_n if vector_top_n is not None else config.vector_top_n
        ),
        "attachFlag": config.attach_flag,
        "caller": config.caller,
        "rerankFlag": config.rerank_flag,
        "reWriteFlag": config.rewrite_flag,
        "rerankTopN": config.rerank_top_n,
        "rerankRuleCode": config.rerank_rule_code,
        "qaType": config.qa_type,
        "vectorMinScore": (
            config.vector_min_score
            if config.vector_min_score is not None
            else 0
        ),
    }

    if effective_space_code_list:
        param["spaceCodeList"] = effective_space_code_list
    if effective_team_space_code_list:
        param["teamSpaceCodeList"] = effective_team_space_code_list
    if effective_psnl_space_code_id:
        param["psnlSpaceCodeId"] = effective_psnl_space_code_id
    if effective_psnl_category_id_list:
        param["psnlCategoryIdList"] = effective_psnl_category_id_list
    if effective_customized_tag_list:
        param["customizedTagList"] = effective_customized_tag_list
    if config.source_org_id_list:
        param["sourceOrgIdList"] = config.source_org_id_list
    if config.source_system_list:
        param["sourceSystemList"] = config.source_system_list
    if config.text_min_score is not None:
        param["textMinScore"] = config.text_min_score
    if config.pub_time_start:
        param["pubTimeStart"] = config.pub_time_start
    if config.pub_time_end:
        param["pubTimeEnd"] = config.pub_time_end

    req_message = {
        "REQ_HEAD": {
            "TRANS_PROCESS": "searchKnowledgeCross",
            "TRAN_ID": "",
        },
        "REQ_BODY": {
            "param": param,
        },
    }
    return json.dumps(req_message, ensure_ascii=False)


def _extract_entry_info(entry: dict[str, Any], source_type: str) -> dict[str, Any]:
    content = str(entry.get("content") or "")
    score_raw = entry.get("score")
    file_name = str(entry.get("fileName") or "")
    para_title = str(entry.get("paraTitle") or "")
    file_id = str(entry.get("fileId") or "")
    para_id = str(entry.get("paraId") or "")
    pub_time = str(entry.get("pubTime") or "")
    create_time = str(entry.get("createTime") or "")
    update_time = str(entry.get("updateTime") or "")
    kn_type = str(entry.get("knType") or "")
    customized_tags = entry.get("customizedTags") or []
    scene_codes = entry.get("sceneCodes") or []
    source_org_id = str(entry.get("sourceOrgId") or "")
    rerank_score_raw = entry.get("rerankScore")
    valid_time_start = str(entry.get("validTimeStart") or "")
    valid_time_end = str(entry.get("validTimeEnd") or "")
    task_id = str(entry.get("taskId") or "")
    main_task_id = entry.get("mainTaskId") or []
    from_attachment = entry.get("fromAttachment", False)
    domain_tags = entry.get("domainTags") or []
    sorted_val = entry.get("sorted")
    page = entry.get("page")

    try:
        score = float(score_raw) if score_raw not in (None, "") else None
    except (TypeError, ValueError):
        score = None

    result: dict[str, Any] = {
        "sourceType": source_type,
        "content": content,
        "score": score,
        "title": para_title or file_name or "无标题",
        "fileName": file_name,
        "fileId": file_id,
        "paraId": para_id,
        "pubTime": pub_time,
        "createTime": create_time,
        "updateTime": update_time,
        "knType": kn_type,
        "customizedTags": customized_tags,
        "sceneCodes": scene_codes,
        "sourceOrgId": source_org_id,
        "validTimeStart": valid_time_start,
        "validTimeEnd": valid_time_end,
        "taskId": task_id,
        "mainTaskId": main_task_id,
        "fromAttachment": from_attachment,
        "domainTags": domain_tags,
    }

    if sorted_val is not None:
        result["sorted"] = sorted_val
    if page is not None:
        result["page"] = page

    if rerank_score_raw is not None:
        try:
            result["rerankScore"] = float(rerank_score_raw)
        except (TypeError, ValueError):
            pass

    return result


def _extract_results(
    payload: dict[str, Any],
    keyword: str,
) -> list[dict[str, Any]]:
    rsp_body = payload.get("RSP_BODY", payload)
    result_data = rsp_body.get("result", rsp_body)

    all_results: list[dict[str, Any]] = []

    text_list = result_data.get("textGroupList", [])
    if isinstance(text_list, list):
        all_results.extend(_extract_entry_info(e, "text") for e in text_list)

    vector_list = result_data.get("vectorGroupList", [])
    if isinstance(vector_list, list):
        all_results.extend(
            _extract_entry_info(e, "vector") for e in vector_list
        )

    rerank_list = result_data.get("rerankGroupList", [])
    if isinstance(rerank_list, list):
        all_results.extend(_extract_entry_info(e, "rerank") for e in rerank_list)

    if not all_results:
        return [{"info": f"未找到相关内容。关键词: {keyword}"}]

    def _sort_key(r: dict) -> float:
        if "rerankScore" in r:
            return r["rerankScore"]
        return r.get("score") or 0.0

    all_results.sort(key=_sort_key, reverse=True)
    return all_results


async def search_cross_backend(
    keyword: str,
    *,
    search_type: str | None = None,
    user_code: str | None = None,
    space_code_list: list[str] | None = None,
    team_space_code_list: list[str] | None = None,
    psnl_space_code_id: str | None = None,
    customized_tag_list: list[str] | None = None,
    psnl_category_id_list: list[str] | None = None,
    text_top_n: int | None = None,
    vector_top_n: int | None = None,
) -> str:
    config = get_cross_search_config()
    if not config.api_url:
        raise ValueError(
            "cross_search.api_url is required. 请在 config.yaml 中配置。",
        )
    if not config.caller:
        raise ValueError(
            "cross_search.caller is required. 请在 config.yaml 中配置。",
        )

    req_message = _build_req_message(
        keyword,
        config,
        search_type=search_type,
        user_code=user_code,
        space_code_list=space_code_list,
        team_space_code_list=team_space_code_list,
        psnl_space_code_id=psnl_space_code_id,
        customized_tag_list=customized_tag_list,
        psnl_category_id_list=psnl_category_id_list,
        text_top_n=text_top_n,
        vector_top_n=vector_top_n,
    )

    headers = dict(config.headers)
    headers.pop("Content-Type", None)

    files = {"REQ_MESSAGE": (None, req_message)}

    async with httpx.AsyncClient(timeout=config.timeout) as client:
        response = await client.post(
            config.api_url,
            headers=headers,
            files=files,
        )
        response.raise_for_status()

    logger.debug("Cross search raw response body: %s", response.text)

    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError(f"cross search returned invalid JSON: {exc}") from exc

    rsp_head = payload.get("RSP_HEAD", {})
    if rsp_head.get("TRAN_SUCCESS") != "1":
        process_status = rsp_head.get("PROCESS_STATUS_CODE", "")
        detail = payload.get("RSP_BODY", {}).get("error", "")
        raise ValueError(
            f"cross search failed (status={process_status}): {detail}",
        )

    results = _extract_results(payload, keyword)
    return json.dumps(results, indent=2, ensure_ascii=False)


async def _cross_search_tool_impl(
    keyword: str,
    search_type: str | None = None,
    user_code: str | None = None,
    space_code_list: list[str] | None = None,
    team_space_code_list: list[str] | None = None,
    psnl_space_code_id: str | None = None,
    customized_tag_list: list[str] | None = None,
    psnl_category_id_list: list[str] | None = None,
    text_top_n: int | None = None,
    vector_top_n: int | None = None,
) -> str:
    """跨知识搜索

    跨场景、团队、个人知识库进行混合召回搜索，支持全文检索和向量检索。

    当需要在多个知识空间中检索信息、或需要跨不同来源的知识进行综合查询时，
    使用本工具。本工具支持同时搜索场景知识库、团队知识库和个人知识库，
    结果涵盖向量匹配和全文匹配两个维度。

    构造搜索关键词时，应贴近用户原始措辞，提取核心主题词，不要自动添加
    泛化限定词，除非用户明确提及或确实需要消歧。

    关于 spacecode 参数的强制规则：
    - 如果系统提示词中指定了 spacecode，你必须原样传入该值，
      禁止省略、修改或替换
    - 至少需要提供 space_code_list、team_space_code_list 或
      psnl_space_code_id 中的一个

    Args:
        keyword: 用户的完整查询语句。禁止提取关键词或修改用户输入、
            必须严格使用用户提供的完整语句。
        search_type: 搜索类型。0=混合检索(默认)，1=全文检索，2=向量检索。
            不传则使用配置默认值。
        user_code: 用户编码。通常由系统自动填充，无需手动传入。
        space_code_list: 场景知识空间代码列表，例如 ["SP0000001",
            "SP0000002"]。不传则使用配置默认值。
        team_space_code_list: 团队知识空间代码列表。不传则使用配置默认值。
        psnl_space_code_id: 个人知识空间代码ID。通常由系统自动填充，
            无需手动传入。
        customized_tag_list: 自定义标签列表，用于过滤搜索结果。
        psnl_category_id_list: 个人知识分类ID列表。
        text_top_n: 全文检索返回条数。不传则使用配置默认值。
        vector_top_n: 向量检索返回条数。不传则使用配置默认值。
    """
    try:
        return await search_cross_backend(
            keyword,
            search_type=search_type,
            user_code=user_code,
            space_code_list=space_code_list,
            team_space_code_list=team_space_code_list,
            psnl_space_code_id=psnl_space_code_id,
            customized_tag_list=customized_tag_list,
            psnl_category_id_list=psnl_category_id_list,
            text_top_n=text_top_n,
            vector_top_n=vector_top_n,
        )
    except httpx.TimeoutException:
        logger.error("Cross search request timed out.", exc_info=True)
        return json.dumps([{"error": "跨知识搜索请求超时。"}], ensure_ascii=False)
    except httpx.HTTPError as exc:
        logger.error("Cross search request failed: %s", exc, exc_info=True)
        return json.dumps(
            [{"error": f"跨知识搜索请求失败: {exc}"}],
            ensure_ascii=False,
        )
    except ValueError as exc:
        return json.dumps([{"error": f"{exc}"}], ensure_ascii=False)
    except Exception as exc:
        logger.error("Unexpected cross search error: %s", exc, exc_info=True)
        return json.dumps(
            [{"error": f"跨知识搜索失败: {exc}"}],
            ensure_ascii=False,
        )


if FunctionTool is not None and ToolMiddlewareBase is not None:
    cross_search_tool = FunctionTool(
        _cross_search_tool_impl,
        # 工具函数名必须是 ^[a-zA-Z0-9_-]+$（DeepSeek 等 API 强校验），
        # 不能用中文名「行内搜索」；中文语义放在 docstring 描述里，
        # 模型通过描述识别该工具。与 deer-search-mcp 的 vector_search
        # 命名风格保持一致。
        name="cross_search",
        is_read_only=True,
        middlewares=[_SpacecodeOverrideMiddleware()],
    )
else:
    # agentscope 不可用时的降级：保持裸函数（与项目 registry.py 风格一致）
    cross_search_tool = _cross_search_tool_impl


__all__ = ["cross_search_tool", "search_cross_backend"]
