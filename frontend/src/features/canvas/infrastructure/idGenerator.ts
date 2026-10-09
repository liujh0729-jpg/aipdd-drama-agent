// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { v4 as uuidv4 } from 'uuid';

import type { IdGenerator } from '../application/ports';

export const uuidGenerator: IdGenerator = {
  next: () => uuidv4(),
};
