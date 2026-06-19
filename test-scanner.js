/**
 * Integration test for Scanner — runs directly with Node.js
 * Usage: node test-scanner.js
 */

const path = require('path');

// We need to require the built scanner
const { Scanner } = require('./dist/main/main/scanner');

const PASS = '\x1b[32m✓\x1b[0m';
const FAIL = '\x1b[31m✗\x1b[0m';
let passed = 0;
let failed = 0;

function assert(condition, name) {
  if (condition) {
    console.log(`  ${PASS} ${name}`);
    passed++;
  } else {
    console.log(`  ${FAIL} ${name}`);
    failed++;
  }
}

async function testBasicScan() {
  console.log('\n--- Test: Basic scan ---');
  const scanner = new Scanner('/tmp', () => {});
  const result = await scanner.scan();

  assert(typeof result.totalUsed === 'number', 'totalUsed is a number');
  assert(typeof result.scannedItems === 'number', 'scannedItems is a number');
  assert(typeof result.scanTime === 'number', 'scanTime is a number');
  assert(Array.isArray(result.topDirs), 'topDirs is an array');
  assert(Array.isArray(result.topFiles), 'topFiles is an array');
  assert(Array.isArray(result.extStats), 'extStats is an array');
  assert(Array.isArray(result.junkDirs), 'junkDirs is an array');
  assert(typeof result.ageGroups === 'object', 'ageGroups is an object');
  assert(typeof result.dirSizeCache === 'object', 'dirSizeCache is an object');
  assert(Array.isArray(result.duplicates), 'duplicates is an array');
  assert(result.scannedItems >= 0, 'scannedItems >= 0');
  assert(result.scanTime >= 0, 'scanTime >= 0');
}

async function testTopN() {
  console.log('\n--- Test: topN option ---');
  const scanner = new Scanner('/tmp', () => {}, { topN: 3 });
  const result = await scanner.scan();

  assert(result.topDirs.length <= 3, `topDirs.length <= 3 (got ${result.topDirs.length})`);
  assert(result.topFiles.length <= 3, `topFiles.length <= 3 (got ${result.topFiles.length})`);
  assert(result.extStats.length <= 3, `extStats.length <= 3 (got ${result.extStats.length})`);
}

async function testExcludeDirs() {
  console.log('\n--- Test: excludeDirs option ---');
  const scanner = new Scanner('/tmp', () => {}, {
    excludeDirs: ['/tmp/exclude_test_dir_that_does_not_exist'],
  });
  const result = await scanner.scan();

  assert(result.scannedItems >= 0, 'scan completes with excludeDirs set');
}

async function testCustomJunkDirs() {
  console.log('\n--- Test: customJunkDirs option ---');
  const scanner = new Scanner('/tmp', () => {}, {
    customJunkDirs: ['/tmp'],
  });
  const result = await scanner.scan();

  // /tmp should appear in junkDirs since we added it as custom
  const found = result.junkDirs.some(([dir]) => dir === '/tmp');
  assert(found, '/tmp appears in junkDirs when set as customJunkDir');
}

async function testProgress() {
  console.log('\n--- Test: progress callback ---');
  let progressCalled = false;
  const scanner = new Scanner('/tmp', (p) => {
    progressCalled = true;
    assert(typeof p.currentPath === 'string', 'progress.currentPath is string');
    assert(typeof p.scannedItems === 'number', 'progress.scannedItems is number');
    assert(typeof p.scannedSize === 'number', 'progress.scannedSize is number');
    assert(typeof p.elapsed === 'number', 'progress.elapsed is number');
  });
  await scanner.scan();
  assert(progressCalled, 'progress callback was called');
}

async function testAbort() {
  console.log('\n--- Test: abort ---');
  const scanner = new Scanner('/', () => {});
  scanner.abort();
  const result = await scanner.scan();
  assert(result.scannedItems === 0, 'aborted scan returns 0 items');
}

async function testDuplicateDetection() {
  console.log('\n--- Test: duplicate detection option ---');
  const scanner = new Scanner('/tmp', () => {}, { enableDupDetection: true });
  const result = await scanner.scan();

  assert(Array.isArray(result.duplicates), 'duplicates is array with dup detection on');
  // Don't assert duplicates.length > 0 since /tmp may not have dupes
}

async function testPlatformJunkPaths() {
  console.log('\n--- Test: platform-aware junk paths ---');
  const scanner = new Scanner('/tmp', () => {});
  const result = await scanner.scan();

  // On Linux, /tmp should be detected as junk
  if (process.platform === 'linux') {
    const hasTmp = result.junkDirs.some(([dir]) => dir === '/tmp');
    assert(hasTmp, '/tmp detected as junk on Linux');
  }
}

(async () => {
  console.log('Scanner Integration Tests');
  console.log('=========================');

  try {
    await testBasicScan();
    await testTopN();
    await testExcludeDirs();
    await testCustomJunkDirs();
    await testProgress();
    await testAbort();
    await testDuplicateDetection();
    await testPlatformJunkPaths();
  } catch (e) {
    console.error('\nTest error:', e);
    failed++;
  }

  console.log(`\n=========================`);
  console.log(`Results: ${passed} passed, ${failed} failed`);
  process.exit(failed > 0 ? 1 : 0);
})();
