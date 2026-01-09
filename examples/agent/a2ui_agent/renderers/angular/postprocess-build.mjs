/*
 Copyright 2025 Google LLC

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

      https://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
 */

import { readFileSync, writeFileSync } from 'fs';
import { join } from 'path';

// Locally we depend on the Lit package via a relative path so we can test it from source.
// This breaks when published to npm so the following script updates the version to the npm one.

const dirname = import.meta.dirname;
const litVersion = parsePackageJson(join(dirname, '../lit/package.json')).version;

if (!litVersion) {
  throw new Error('Cannot determine version of Lit package');
}

const packageJsonPath = join(dirname, './dist/package.json');
const packageJson = parsePackageJson(packageJsonPath);

if (!packageJson.dependencies['@a2ui/lit']) {
  throw new Error(
    'Angular package does not depend on the Lit library. ' +
      'Either update the package.json or remove this script.',
  );
}

packageJson.dependencies['@a2ui/lit'] = '^' + litVersion;
writeFileSync(packageJsonPath, JSON.stringify(packageJson, undefined, 2));

function parsePackageJson(path) {
  const content = readFileSync(path, 'utf8');
  return JSON.parse(content);
}
