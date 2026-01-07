/**
 * Copyright 2019 Google LLC. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const rewire = require("rewire");
var artifactRegistryAuth = rewire('../src/auth');
var update = rewire('../src/update');

const creds = 'abcd';

function getTestDir(test) {
  return path.join(os.tmpdir(), test);
}

// Writing to a file whose name starts with a dot is tricky on Windows, thus
// changing the name of the config file to "npmrc".
function getConfigPath(test) {
  return path.join(os.tmpdir(), test, '/npmrc');
}

describe('#auth', () => {
  describe('#getCreds', () => {
    it('gets application default credentials first', async () => {
      artifactRegistryAuth.__set__('getApplicationDefaultCredentials', function(){
        return Promise.resolve("valid-token-adc")});
      artifactRegistryAuth.__set__('getGcloudCredentials', function(){
        return Promise.resolve("valid-token-gcloud")});
      let gotCreds = await artifactRegistryAuth.getCreds();
      assert.equal(gotCreds, "valid-token-adc");
    });

    it ('gets gcloud credentials if application default credentials do not exist', async() => {
      artifactRegistryAuth.__set__('getApplicationDefaultCredentials', function(){
        throw new Error("invalid-token-adc")});
      artifactRegistryAuth.__set__('getGcloudCredentials', function(){
        return Promise.resolve("valid-token-gcloud")});
      let gotCreds = await artifactRegistryAuth.getCreds();
      assert.equal(gotCreds, "valid-token-gcloud");
    });

    it ('errors if neither adc or gcloud creds are valid', async() => {
      artifactRegistryAuth.__set__('getApplicationDefaultCredentials', function(){
          throw new Error("invalid-token-adc")});
      artifactRegistryAuth.__set__('getGcloudCredentials', function(){
          throw new Error("invalid-token-adc")});
      assert.rejects(artifactRegistryAuth.getCreds(), {
        name: 'Error',
        message: 'Fail to get credentials. Please run: \n' +
            '`gcloud auth application-default login`, `gcloud auth login`, or \n' +
            '`export GOOGLE_APPLICATION_CREDENTIALS=<path/to/service/account/key>`'
      });
    });
  });
});
