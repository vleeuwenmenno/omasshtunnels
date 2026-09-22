const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const model = vm.createContext({});
vm.runInContext(fs.readFileSync(`${__dirname}/../Model.js`, 'utf8'), model);
const rows = [
  {id: 'a', name: 'Production DB', alias: 'prod-db', localPort: 15432, starred: false, active: true},
  {id: 'b', name: 'Dev API', alias: 'dev-api', remotePort: 8000, starred: true, active: false}
];
const ids = rows => Array.from(rows, row => row.id);
assert.deepEqual(ids(model.tunnels(rows, '', 'all')), ['b', 'a']);
assert.deepEqual(ids(model.tunnels(rows, '', 'starred')), ['b']);
assert.deepEqual(ids(model.tunnels(rows, '', 'active')), ['a']);
assert.deepEqual(ids(model.tunnels(rows, 'PROD 15432', 'all')), ['a']);
assert.deepEqual(ids(model.tunnels(rows, 'missing', 'all')), []);
assert.equal(model.aliases([{alias: 'dev-api', hostname: 'example.test'}], 'DEV').length, 1);
assert.equal(model.details({localPort: 1234, remoteHost: 'localhost', remotePort: 5432}), '127.0.0.1:1234  →  localhost:5432');
assert.equal(model.details({configured: true, forwards: [{kind: 'dynamicforward', value: '1080'}]}), 'SOCKS: 1080');
console.log('8 search, filter, sort, and display checks passed.');
