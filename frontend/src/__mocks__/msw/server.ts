// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { setupServer } from "msw/node";
import { handlers } from "./handlers/tasks";

export const server = setupServer(...handlers);
