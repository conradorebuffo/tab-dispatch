'use strict';
// Node 24 treats "node --test <directory>" as a module to resolve, not as a test folder.
// This index lets `node --test dev/TabDispatch/test/` run the suite.
require('./match.test.js');
