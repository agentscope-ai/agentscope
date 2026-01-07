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


const registryARRegex = /(@[a-zA-Z0-9-*~][a-zA-Z0-9-*._~]*:)?registry=https:(\/\/[a-zA-Z0-9-]+[-]npm[.]pkg[.]dev\/.*\/)/;
const authTokenARRegex = /(\/\/[a-zA-Z0-9-]+[-]npm[.]pkg[.]dev\/.*\/):_authToken=(.*)/;
const passwordARRegex = /(\/\/[a-zA-Z0-9-]+[-]npm[.]pkg[.]dev\/.*\/):_password=(.*)/;

const registryAllDomainRegex = /(@[a-zA-Z0-9-*~][a-zA-Z0-9-*._~]*:)?registry=https:(\/\/.*)/;
const authTokenAllDomainRegex = /(\/\/.*\/):_authToken=(.*)/;
const passwordAllDomainRegex = /(\/\/.*\/):_password=(.*)/;


const configType = {
  Default: "Default",
  Registry: "Registry",
  AuthToken: "AuthToken",
  Password: "Password",
}

function parseConfig(text, allowAllDomains) {
  registryRegex = registryARRegex;
  authTokenRegex = authTokenARRegex;
  passwordRegex = passwordARRegex;
  if (allowAllDomains) {
    registryRegex = registryAllDomainRegex;
    authTokenRegex = authTokenAllDomainRegex;
    passwordRegex = passwordAllDomainRegex;
  }

  let m = text.match(registryRegex);
  if (m) {
    return {
      type: configType.Registry,
      scope: m[1] ? m[1].replace(':', '') : m[1],
      registry: m[2],
      toString: function() {
        return `${this.scope ? this.scope + ':' : ''}registry=https:${this.registry}`;
      }
    }
  }
  m = text.match(authTokenRegex);
  if (m) {
    return {
      type: configType.AuthToken,
      registry: m[1],
      token: m[2],
      toString: function() {
        return `${this.registry}:_authToken=${this.token}`;
      }
    }
  }
  m = text.match(passwordRegex);
  if (m) {
    return {
      type: configType.Password,
      registry: m[1],
      password: m[2],
      toString: function() {
        return `${this.registry}:_password=${this.password}`;
      }
    }
  }
  return {
    type: configType.Default,
    toString: function() {
      return text;
    }
  }
}

module.exports = {
  configType,
  parseConfig
};
