# -*- coding: utf-8 -*-
# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "AgentScope"
copyright = "2025, Alibaba"
author = "Alibaba Tongyi Lab"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "myst_parser",
    "sphinx_gallery.gen_gallery",
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
]

myst_enable_extensions = [
    "colon_fence",
]

sphinx_gallery_conf = {
    "download_all_examples": False,
    "examples_dirs": [
        "src",
    ],
    "gallery_dirs": [
        "tutorial",
    ],
    "filename_pattern": "src/.*\.py",
    "example_extensions": [".py"],
}

templates_path = ["../_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

languages = ["en", "zh_CN"]
language = "en"

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "furo"
html_title = (
    "<span style='font-weight: 700; color: #2196f3;'>AgentScope</span>"
)
html_logo = "../_static/images/logo.svg"
html_favicon = "../_static/images/logo.svg"
html_static_path = ["../_static"]
html_css_files = [
    "css/gallery.css",
]

html_js_files = [
    "language_switch.js",
]

html_theme_options = {
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/agentscope-ai/agentscope",
            "html": """
                <svg stroke="currentColor" fill="currentColor" stroke-width="0" viewBox="0 0 16 16">
                    <path fill-rule="evenodd" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"></path>
                </svg>
            """,
            "class": "",
        },
        {
            "name": "Discord",
            "url": "https://discord.gg/eYMpfnkG8h",
            "html": """
                <svg stroke="currentColor" fill="currentColor" stroke-width="0" t="1753331148815" class="icon" viewBox="0 0 1024 1024" version="1.1" xmlns="http://www.w3.org/2000/svg" p-id="5721" width="200" height="200">
                    <path d="M723.903423 359.138018c-69.65045-52.952793-136.256577-51.476757-136.256576-51.476757l-6.088649 7.564685c83.027027 25.738378 121.127207 62.085766 121.127207 62.085766a387.459459 387.459459 0 0 0-145.297297-46.956397 418.179459 418.179459 0 0 0-98.340901 1.752793 73.801802 73.801802 0 0 1-7.564684 1.476036 357.385225 357.385225 0 0 0-110.702703 30.258739 278.786306 278.786306 0 0 0-28.782703 13.653333S353.049369 339.488288 440.873514 313.657658l-4.612613-6.088649s-66.513874-1.476036-136.164324 51.476757A654.252973 654.252973 0 0 0 230.630631 642.167928s40.867748 71.126486 148.341621 73.801802c0 0 16.697658-22.694054 31.827027-40.867748-62.085766-18.45045-84.77982-57.565405-84.77982-57.565405a130.998198 130.998198 0 0 0 13.653334 7.564684s0 1.568288 1.476036 1.568289c1.476036 1.476036 3.044324 1.476036 4.52036 3.044324a238.748829 238.748829 0 0 0 34.779099 16.605405 513.199279 513.199279 0 0 0 71.218739 21.218018 350.558559 350.558559 0 0 0 125.555315 0 329.894054 329.894054 0 0 0 69.650451-21.218018A247.328288 247.328288 0 0 0 702.685405 618.09009s-24.262342 39.391712-87.824144 57.565405c13.653333 18.45045 31.827027 39.299459 31.827027 39.29946 107.473874-2.952072 148.341622-73.801802 146.773334-72.602523a654.990991 654.990991 0 0 0-69.558199-283.214414zM421.131532 596.77982a54.705586 54.705586 0 0 1 0-109.042162 54.705586 54.705586 0 0 1 0 109.042162z m177.124324 0a54.705586 54.705586 0 1 1 49.908468-54.521081 52.491532 52.491532 0 0 1-49.908468 54.521081z" p-id="5722"></path><path d="M512 1024A512 512 0 1 1 1024 512 512.645766 512.645766 0 0 1 512 1024z m0-972.892252a461.261261 461.261261 0 1 0 461.261261 461.261261 461.261261 461.261261 0 0 0-461.261261-461.261261z" p-id="5723"></path>
                </svg>
            """,
            "class": "",
        },
        {
            "name": "DingTalk",
            "url": "https://qr.dingtalk.com/action/joingroup?code=v1,k1,OmDlBXpjW+I2vWjKDsjvI9dhcXjGZi3bQiojOq3dlDw=&_dt_no_comment=1&origin=11",
            "html": """
                <svg stroke="currentColor" fill="currentColor" stroke-width="0" viewBox="0 0 1024 1024">
                    <path d="M512 0C229.205333 0 0 229.205333 0 512s229.205333 512 512 512 512-229.205333 512-512S794.794667 0 512 0z m237.312 480.810667c-1.109333 4.48-3.712 11.093333-7.424 18.986666h0.128l-0.426667 0.682667c-21.504 46.037333-77.610667 136.106667-77.610666 136.106667l-0.298667-0.597334-16.384 28.501334h79.018667l-150.912 200.917333 34.304-136.533333h-62.208l21.589333-90.282667c-17.493333 4.224-38.101333 10.026667-62.592 17.92 0 0-33.109333 19.370667-95.317333-37.333333 0 0-41.984-36.992-17.578667-46.165334 10.410667-3.925333 50.304-8.917333 81.706667-13.226666 42.410667-5.674667 68.48-8.789333 68.48-8.789334s-130.773333 2.005333-161.792-2.901333c-30.976-4.906667-70.4-56.704-78.805334-102.186667 0 0-12.970667-25.002667 27.904-13.226666 40.917333 11.818667 210.005333 46.08 210.005334 46.08S321.109333 411.434667 306.517333 394.922667c-14.634667-16.469333-43.093333-89.770667-39.424-134.869334 0 0 1.621333-11.221333 13.098667-8.192 0 0 162.602667 74.282667 273.792 114.986667 111.104 40.704 207.786667 61.397333 195.328 114.005333z" opacity=".65" p-id="6077"></path>
                </svg>
            """,
            "class": "",
        },
    ],
    "light_css_variables": {
        "color-brand-primary": "#2196f3",
        "color-brand-content": "#2196f3",
        "color-admonition-background": "#f8f9fa",
    },
    "dark_css_variables": {
        "color-link": "#2196f3",
        "color-link--hover": "#2196f3",
        "color-brand-primary": "#64b5f6",
        "color-brand-content": "#64b5f6",
    },
}

source_suffix = [".md", ".rst"]


# -- Options for API documentation -------------------------------------------

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_class_signature = "separated"
autodoc_default_options = {
    "special-members": "__call__",
}

add_module_names = False
python_display_short_literal_types = True


def skip_member(app, what, name, obj, skip, options):
    if name in [
        "__call__",
        "_format",
        "_format_agent_message",
        "_format_tool_sequence",
    ]:
        return False

    return skip


def setup(app):
    app.connect("autodoc-skip-member", skip_member)
