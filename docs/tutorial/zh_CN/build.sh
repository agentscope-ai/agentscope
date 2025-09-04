#!/bin/bash

set -e

# Generate the API rst files
sphinx-apidoc -o api ../../../src/agentscope -t ../_templates -e

# Build the html
sphinx-build -M html ./ build