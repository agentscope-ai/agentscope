# -*- coding: utf-8 -*-
"""parsing and digesting the web pages"""
import json
from urllib.parse import urlparse
from typing import Optional, Callable, Sequence, Any
import requests
from loguru import logger


from agentscope.service.service_response import ServiceResponse
from agentscope.service.service_status import ServiceExecStatus
from agentscope.base import ModelWrapperBase
from agentscope.service import summarization


DEFAULT_WEB_SYS_PROMPT = (
    "You're a web page analyser. You job is to extract important"
    "and useful information from html or webpage description.\n"
)


def is_valid_url(url: str) -> bool:
    """
    Use urlparse to check if a URL is valid
    Args:
        url (str): string to be checked

    Returns:
        bool: True if url is valid, False otherwise
    """
    try:
        result = urlparse(url)
        # Check if the URL has both a scheme
        # (e.g., "http" or "https") and a netloc (domain).
        return all([result.scheme, result.netloc])
    except ValueError:
        return False  # A ValueError indicates that the URL is not valid.


def load_web(
    url: str,
    keep_raw: bool = True,
    html_selected_tags: Optional[Sequence[str]] = None,
    self_parse_func: Optional[Callable[[requests.Response], Any]] = None,
    timeout: int = 5,
) -> ServiceResponse:
    """Function for parsing and digesting the web page.

    Args:
        url (str): the url of the web page
        keep_raw (bool):
            Whether to keep raw HTML. If True, the content is
            stored with key "raw".
        html_selected_tags (Optional[Sequence[str]]):
            the text in elements of `html_selected_tags` will
            be extracted and stored with "html_to_text"
            key in return.
        self_parse_func (Optional[Callable]):
            if "self_parse_func" is not None, then the
            function will be invoked with the
            requests.Response as input.
            The result is stored with `self_define_func`
            key
        timeout (int): timeout parameter for requests.

    Returns:
        `ServiceResponse`: If successful, `ServiceResponse` object is returned
        with `content` field is a dict, where keys are subset of:

            "raw": exists if `keep_raw` is True, store raw HTML content`;

            "self_define_func": exists if `self_parse_func` is provided,
            store the return of self_define_func;

            "html_to_text": exists if `html_selected_tags` is provided
            and not empty;

            "json": exists if url links to a json webpage, then it is
            parsed as json.

         For example, `ServiceResponse.content` field is

        .. code-block:: python

            {
                "raw": xxxxx,
                "selected_tags_text": xxxxx
            }
    """
    header = {
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"
        " AppleWebKit/537.36 (KHTML, like Gecko) ",
    }
    try:
        response = requests.get(url=url, headers=header, timeout=timeout)

        if response.status_code == 200:
            results = {}
            if keep_raw:
                results["raw"] = response.content

            if self_parse_func:
                results["self_define_func"] = self_parse_func(response)

            content_type = response.headers["Content-Type"].lower()
            if "html" in content_type and html_selected_tags:
                html_clean_text = parse_html_to_text(
                    response.text,
                    html_selected_tags,
                )
                results["html_to_text"] = html_clean_text
            elif "pdf" in content_type:
                # TODO: support pdf in the future
                logger.warning(
                    "Current version does not parse url with pdf "
                    "Content-Types",
                )
            elif "json" in content_type:
                results["json"] = json.loads(response.text)
            elif "image" in content_type:
                # TODO: to support image (gif, jpeg, png) data
                logger.warning(
                    "Current implementation returns binary "
                    "response.content for url with image Content-Types",
                )
            else:
                raise NotImplementedError(
                    f"Unsupported content type ({content_type}) "
                    f"with url: ({url})",
                )

            return ServiceResponse(
                ServiceExecStatus.SUCCESS,
                content=results,
            )
        else:
            logger.warning(
                f"Fail to load web page, "
                f"status code {response.status_code}",
            )
            return ServiceResponse(
                ServiceExecStatus.ERROR,
                content="",
            )
    except Exception as e:
        logger.warning(e)
        return ServiceResponse(ServiceExecStatus.ERROR, content="")


def parse_html_to_text(
    html_text: str,
    html_selected_tags: Optional[Sequence[str]] = None,
) -> str:
    """
    Parse the obtained HTML file.

    Args:
        html_text (str):
            HTML source code
        html_selected_tags (Optional[Sequence[str]]):
            the text in elements of `html_selected_tags` will
            be extracted and returned.

    Returns:
        `ServiceResponse`: If successful, `ServiceResponse` object is returned
        with `content` field is processed text content of the selected tags,
    """
    if html_selected_tags:
        logger.info(
            f"extracting text information from tags: " f"{html_selected_tags}",
        )
        try:
            from bs4 import BeautifulSoup, NavigableString, Tag
        except ImportError as exc:
            raise ImportError(
                "BeautifulSoup4 is required for processing the "
                "web page without model."
                "Please install with `pip install bs4` .",
            ) from exc

        doc = BeautifulSoup(html_text, "html.parser")

        def get_navigable_strings(
            e: Tag,
        ) -> str:
            # pylint: disable=cell-var-from-loop
            text = ""
            for child in e.children:
                if isinstance(child, Tag):
                    # pylint: disable=cell-var-from-loop
                    text += get_navigable_strings(child).strip(" \n\t")
                elif isinstance(child, NavigableString):
                    if (e.name == "a") and (href := e.get("href")):
                        if is_valid_url(href):
                            text += f"[{child.strip()}]({href})"
                    else:
                        text += child.text
            return " ".join(text.split())

        text_parts = ""
        for element in doc.find_all(recursive=True):
            if element.name in html_selected_tags:
                text_parts += get_navigable_strings(element).strip(" \n\t")
                element.decompose()
    else:
        text_parts = ""

    return text_parts


def digest_webpage(
    web_text_or_url: str,
    model: ModelWrapperBase = None,
    html_selected_tags: Sequence[str] = ("h", "p", "li", "div", "a"),
    digest_prompt: str = DEFAULT_WEB_SYS_PROMPT,
) -> ServiceResponse:
    """Digest the given webpage.

    Args:
        web_text_or_url (str): preprocessed web text or url to the web page
        model (ModelWrapperBase): the model to digest the web content
        html_selected_tags (Sequence[str]):
            the text in elements of `html_selected_tags` will
            be extracted and feed to the model
        digest_prompt (str): system prompt for the model to digest
            the web content

    Returns:
        `ServiceResponse`: If successful, `ServiceResponse` object is returned
        with `content` field filled with the model output.
    """
    if is_valid_url(web_text_or_url):
        # if an url is provided, then
        # load the content of the url first
        if html_selected_tags is None or len(html_selected_tags) == 0:
            html_selected_tags = ["h", "p", "li", "div", "a"]
        response = load_web(
            url=web_text_or_url,
            html_selected_tags=html_selected_tags,
        )
        if response.status == ServiceExecStatus.SUCCESS:
            web_text = response.content["html_to_text"]
        else:
            return response
    else:
        web_text = web_text_or_url
    return summarization(
        model=model,
        text=web_text,
        system_prompt=digest_prompt,
    )
