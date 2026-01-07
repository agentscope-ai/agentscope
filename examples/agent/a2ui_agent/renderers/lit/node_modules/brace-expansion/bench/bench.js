/* global bench */

import expand from '..'
import fs from 'fs'

const resfile = new URL('../test/cases.txt', import.meta.url)
const cases = fs.readFileSync(resfile, 'utf8').split('\n')

bench('Average', function () {
  cases.forEach(function (testcase) {
    expand(testcase)
  })
})
