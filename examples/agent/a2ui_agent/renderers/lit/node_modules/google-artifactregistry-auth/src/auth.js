// Copyright 2019 Google LLC
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

const {GoogleAuth} = require('google-auth-library');
const util = require('util');
const exec = util.promisify(require('child_process').exec);
const {logger} = require('./logger');

/**
 * Automatically choose the right client credentials based on the environment.
 *
 * @return {!Promise<string>} cred Encrypted access token.
 */
async function getCreds() {
  try {
    logger.log(`Retrieving application default credentials...`)
    const creds = await getApplicationDefaultCredentials();
    return creds;
  } catch (err) {
    logger.debug(`Failed to retrieve application default credentials: ${err.message}. Fall back to gcloud credentials.`);
  }
  try {
    logger.log(`Retrieving credentials from gcloud...`)
    const creds = await getGcloudCredentials();
    return creds;
  } catch (err) {
    logger.debug(`Failed to retrieve credentials from gcloud: ${err.message}.`)
  }
  throw new Error(
      'Fail to get credentials. Please run: \n' +
      '`gcloud auth application-default login`, `gcloud auth login`, or \n' +
      '`export GOOGLE_APPLICATION_CREDENTIALS=<path/to/service/account/key>`');
}

/**
 * Retrieves the application default credentials.
 *
 * @return {!Promise<string>} cred Encrypted access token.
 */
async function getApplicationDefaultCredentials() {
  const auth = new GoogleAuth({
    scopes: 'https://www.googleapis.com/auth/cloud-platform',
    projectId: 'unused-project'
  });
  const client = await auth.getClient();
  return (await client.getAccessToken()).token;
}

/**
 * Retrieves the credentials from the current active account from gcloud.
 *
 * @return {!Promise<string>} cred Encrypted access token.
 */
async function getGcloudCredentials() {
  const isWindows = process.platform === 'win32';
  let gcloud = isWindows? "gcloud.cmd" : "gcloud";
  const {stdout, stderr} = await exec(`${gcloud} auth print-access-token`);
  // the token from gcloud auth print-access-token has a newline in the end
  return stdout.trim();
}


module.exports = {
  getCreds
};
