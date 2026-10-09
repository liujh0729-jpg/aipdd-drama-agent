const fs = require('fs');
const path = require('path');

const base = process.cwd();
const EOL = /\r\n/.test(fs.readFileSync(path.join(base, 'src/routeTree.gen.ts'), 'utf8').substring(0, 100)) ? '\r\n' : '\n';

// Fix 1: EleventhFaqScreen.tsx - remove unused loginStyles import
let f1Path = path.join(base, 'src/components/login/cinematic/EleventhFaqScreen.tsx');
let f1 = fs.readFileSync(f1Path, 'utf8');
f1 = f1.replace(/^import loginStyles from.*\r?\n/m, '');
fs.writeFileSync(f1Path, f1, 'utf8');
console.log('Fixed EleventhFaqScreen.tsx - removed unused loginStyles import');

// Fix 2: LoginCinematicHero.tsx - remove unused MessageCircle from import
let f2Path = path.join(base, 'src/components/login/cinematic/LoginCinematicHero.tsx');
let f2 = fs.readFileSync(f2Path, 'utf8');
f2 = f2.replace(/MessageCircle, /g, '');
fs.writeFileSync(f2Path, f2, 'utf8');
console.log('Fixed LoginCinematicHero.tsx - removed unused MessageCircle from import');

// Fix 3: routeTree.gen.ts - add /register to FileRoutesByPath
let f3Path = path.join(base, 'src/routeTree.gen.ts');
let f3 = fs.readFileSync(f3Path, 'utf8');
const registerDecl = "    '/register': {" + EOL +
      "      id: '/register'" + EOL +
      "      path: '/register'" + EOL +
      "      fullPath: '/register'" + EOL +
      "      preLoaderRoute: typeof RegisterRouteImport" + EOL +
      "      parentRoute: typeof rootRouteImport" + EOL +
      "    }" + EOL;
f3 = f3.replace(/(    '\/_app': \{)/, registerDecl + '$1');
fs.writeFileSync(f3Path, f3, 'utf8');
console.log('Fixed routeTree.gen.ts - added /register to FileRoutesByPath');
