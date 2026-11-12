'use strict';
// Node 24 trata "node --test <directorio>" como un módulo a resolver, no como una carpeta de tests.
// Este índice permite que `node --test dev/ClaudeTabDispatch/test/`  corra la suite.
require('./cotejo.test.js');
