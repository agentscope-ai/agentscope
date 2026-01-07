// Copyright 2025 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');
const {logger} = require('./logger');

/**
 * Update the project and user yarnrc.yml files.
 *
 * @param {string} fromConfigPath Path to the yarnrc.yml file to read scope registry configs from, should be the project yarnrc.yml file.
 * @param {string} toConfigPath Path to yarnrc.yml file to write authentication configs to, should be the user yarnrc.yml file.
 * @param {string} creds Encrypted credentials.
 * @return {!Promise<undefined>}
 */
async function updateYarnConfigFiles(fromConfigPath, toConfigPath, creds) {
  fromConfigPath = path.resolve(fromConfigPath);
  toConfigPath = path.resolve(toConfigPath);

  const fromDoc = fs.existsSync(fromConfigPath) ? yaml.load(fs.readFileSync(fromConfigPath, 'utf8')) : {};
  const toDoc = fs.existsSync(toConfigPath)
    ? (yaml.load(fs.readFileSync(toConfigPath, 'utf8')) || {})
    : {};

  if (fromDoc && fromDoc.npmScopes) {
    for (const scope in fromDoc.npmScopes) {
      const fromScope = fromDoc.npmScopes[scope];
      if (fromScope.npmRegistryServer) {
        const registry = fromScope.npmRegistryServer;
        logger.debug(`Found registry ${registry} in ${fromConfigPath}`);
        if (!toDoc.npmScopes) {
          toDoc.npmScopes = {};
        }
        if (!toDoc.npmScopes[scope]) {
          toDoc.npmScopes[scope] = {};
        }
        const toScope = toDoc.npmScopes[scope];
        toScope.npmRegistryServer = registry;
        toScope.npmAlwaysAuth = true;
        toScope.npmAuthToken = creds;
      }
    }
  } else {
    logger.debug(`Not found file ${fromConfigPath}, creating ${toConfigPath}, replace the default values`);
    // Ensure default workspace scope is present if not already set
    if (!toDoc.npmScopes) {
      toDoc.npmScopes = {};
    }
    if (!toDoc.npmScopes.workspace) {
      toDoc.npmScopes.workspace = {
        npmRegistryServer: 'https://<location>-npm.pkg.dev/<project>/<repo>',
        npmAlwaysAuth: true,
        npmAuthToken: creds,
      };
    }
  }

  await fs.promises.writeFile(toConfigPath, yaml.dump(toDoc));
}

module.exports = {
  updateYarnConfigFiles,
};
