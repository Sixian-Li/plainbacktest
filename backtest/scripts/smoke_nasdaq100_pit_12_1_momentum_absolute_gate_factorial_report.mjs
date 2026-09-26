#!/usr/bin/env node
// Route the shared interaction smoke test to this report's primary performance panel.

import {spawnSync} from 'node:child_process';

const reportPath = process.argv[2];
if (!reportPath) throw new Error('report path is required');

const result = spawnSync(
  process.execPath,
  [
    'scripts/smoke_report_ui.mjs',
    reportPath,
    'performance-nasdaq100-absolute-gates-buffer'
  ],
  {stdio: 'inherit'}
);

if (result.error) throw result.error;
process.exit(result.status ?? 1);
