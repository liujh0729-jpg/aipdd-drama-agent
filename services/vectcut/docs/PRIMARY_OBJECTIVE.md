You are working on my Windows development machine.

I want you to fully install, configure, test, and activate the following CapCut MCP server:

GitHub repository:
https://github.com/Atx-Guy/capcut-mcp-server

This is a local MCP server for automating CapCut video editing. It requires the VectCutAPI backend from:

https://github.com/sun-guannan/VectCutAPI

## PRIMARY OBJECTIVE

Set up a completely working local CapCut MCP environment that Antigravity can use directly.

Do NOT just explain the steps to me.

Perform the setup yourself using the terminal, PowerShell, filesystem, Git, npm, Python, and any other required tools.

You have my permission to:

- Create directories
- Clone GitHub repositories
- Install npm dependencies
- Install Python dependencies
- Run npm commands
- Run Python commands
- Build TypeScript projects
- Create and modify MCP configuration files
- Start and stop local development servers
- Inspect files and directories
- Check installed software and versions
- Modify project configuration when necessary
- Test localhost endpoints
- Run MCP server tests
- Fix installation/build/configuration errors
- Retry failed commands
- Use PowerShell commands
- Use administrator privileges when required by the environment
- Make reasonable configuration decisions without asking me unnecessarily

Do NOT ask me for confirmation for normal installation/configuration operations.

Only stop and ask me if an operation would permanently delete unrelated user data, modify Windows security policies, disable security software, expose the machine/network publicly, or require a secret/password/API key that I have not provided.

## STEP 1 — INSPECT THE ENVIRONMENT

First inspect the machine.

Check:

- Windows version
- Node.js version
- npm version
- Python version
- pip version
- Git version
- Whether CapCut is installed
- Whether the required CapCut application is running
- Whether port 9001 is already in use
- Whether port 3000 is already in use
- Current working directories
- Existing MCP configuration
- Whether an existing installation of either repository already exists

Do not overwrite an existing working installation blindly.

If something is already installed, reuse it when appropriate.

## STEP 2 — CHOOSE A CLEAN INSTALL LOCATION

Use a sensible Windows development directory.

Prefer:

C:\Dev\capcut

Create it if necessary.

Use:

C:\Dev\capcut\capcut-mcp-server

for the MCP repository.

Use:

C:\Dev\capcut\VectCutAPI

for the backend.

If an existing installation is detected elsewhere, inspect it first and decide whether it can safely be reused.

## STEP 3 — INSTALL VectCutAPI

Clone:

https://github.com/sun-guannan/VectCutAPI.git

into:

C:\Dev\capcut\VectCutAPI

Inspect the repository before installing anything.

Create/use an appropriate Python virtual environment if the project supports it.

Install all required dependencies from its requirements.txt.

Do not blindly install packages globally if a virtual environment can be used.

Resolve dependency/build errors automatically where reasonably possible.

Then identify the correct command for starting the API server.

The expected backend is:

http://localhost:9001

The repository documentation indicates the server is normally started with:

python capcut_server.py

Verify the actual repository structure before executing it.

## STEP 4 — START AND TEST VectCutAPI

Start the VectCutAPI backend.

Verify that:

http://localhost:9001

is actually responding.

Inspect its available routes/endpoints if necessary.

If port 9001 is already occupied, determine whether the existing process is the correct VectCutAPI process.

Do NOT kill unrelated processes just because they use port 9001.

If the backend fails to start:

1. Read the error.
2. Determine the actual cause.
3. Fix the problem.
4. Restart it.
5. Test again.

Continue until the backend is operational.

## STEP 5 — INSTALL capcut-mcp-server

Clone:

https://github.com/Atx-Guy/capcut-mcp-server

into:

C:\Dev\capcut\capcut-mcp-server

Inspect package.json, tsconfig.json, src, and README before making assumptions.

Run:

npm install

Then:

npm run build

Fix any TypeScript/build/dependency problems automatically.

Verify that:

C:\Dev\capcut\capcut-mcp-server\dist\index.js

exists after the build.

## STEP 6 — CONFIGURE THE MCP SERVER

Configure the MCP server using:

CAPCUT_API_URL=http://localhost:9001

The MCP should run in local stdio mode unless Antigravity specifically requires another transport.

The expected command is conceptually:

node C:\Dev\capcut\capcut-mcp-server\dist\index.js

Create the appropriate Antigravity MCP configuration.

If Antigravity uses an mcp_config.json configuration file, configure it accordingly.

Use this structure as the baseline:

{
  "mcpServers": {
    "capcut": {
      "command": "node",
      "args": [
        "C:\\Dev\\capcut\\capcut-mcp-server\\dist\\index.js"
      ],
      "env": {
        "CAPCUT_API_URL": "http://localhost:9001"
      }
    }
  }
}

IMPORTANT:

Use the actual discovered paths on this machine if they differ.

Do not blindly overwrite an existing MCP configuration.

Merge the CapCut MCP entry into the existing configuration while preserving other MCP servers.

## STEP 7 — GIVE THE MCP FULL LOCAL TOOL ACCESS

Configure the MCP so that Antigravity can use ALL tools exposed by the server.

Do not restrict the tool set unnecessarily.

The repository currently documents these tools:

- capcut_create_draft
- capcut_add_video
- capcut_add_audio
- capcut_add_text
- capcut_add_image
- capcut_add_subtitle
- capcut_add_keyframe
- capcut_add_effect
- capcut_add_sticker
- capcut_save_draft
- capcut_get_duration

Verify the actual tools exposed by the installed build instead of assuming the README is perfectly current.

## STEP 8 — TEST THE MCP DIRECTLY

Do not consider the setup complete merely because npm build succeeds.

Actually launch the MCP server.

Verify that it starts without crashing.

Verify that it can communicate with the VectCutAPI backend.

Verify that Antigravity can discover the CapCut MCP tools.

If MCP initialization fails:

- inspect stderr
- inspect configuration
- inspect environment variables
- inspect Node path
- inspect Python/VectCutAPI status
- fix the issue
- restart and test again

## STEP 9 — TEST A REAL CAPCUT WORKFLOW

After the MCP is connected, perform a minimal non-destructive test.

Create a small draft using the MCP.

Use something like:

1920x1080
30 FPS

Then verify that the MCP can successfully execute at least:

1. capcut_create_draft
2. capcut_get_duration if a suitable local media file exists
3. capcut_save_draft

If suitable local test media exists, perform a more complete test using:

- video
- text
- image
- audio

Do not modify or overwrite unrelated existing CapCut projects.

Use a clearly identifiable test project/draft.

## STEP 10 — CAPCUT APPLICATION

Determine whether CapCut Desktop is installed.

If it is installed, determine its installation path.

Verify that the generated/saved draft can be recognized by CapCut.

Do not assume that "MCP server running" means CapCut itself is correctly integrated.

If the MCP only generates a draft file that must subsequently be opened/imported into CapCut, clearly identify that behavior.

## STEP 11 — CREATE STARTUP SCRIPTS

After the system works, create convenient PowerShell scripts in:

C:\Dev\capcut\

For example:

start-vectcutapi.ps1
start-capcut-mcp.ps1
start-capcut-stack.ps1

The final stack script should:

1. Start VectCutAPI
2. Wait until localhost:9001 is responsive
3. Start the MCP server if appropriate
4. Display useful status information

Do not create scripts that launch duplicate backend processes if one is already running.

## STEP 12 — ERROR HANDLING

If something fails, do not immediately ask me what to do.

Investigate it yourself.

Useful diagnostics include:

- npm logs
- Python traceback
- package versions
- filesystem permissions
- process list
- port availability
- localhost connectivity
- MCP stderr
- TypeScript compiler errors
- Git repository state

Make reasonable fixes automatically.

## STEP 13 — SECURITY BOUNDARY

I am explicitly authorizing full local development permissions for this setup.

You may:

- execute shell commands
- install dependencies
- create files
- modify project files
- modify MCP configuration
- start/stop processes belonging to this setup
- access local development directories
- use localhost services

However, do NOT:

- expose VectCutAPI to the public internet
- bind services to 0.0.0.0 unless absolutely required
- disable Windows Defender
- disable firewall/security protections
- delete unrelated files
- delete unrelated MCP configurations
- modify unrelated applications
- expose credentials or secrets
- upload local files anywhere unnecessarily

Keep the API and MCP local.

## STEP 14 — FINAL VERIFICATION

Before declaring success, verify all of the following:

[ ] Git repositories cloned successfully
[ ] Python dependencies installed
[ ] VectCutAPI starts
[ ] localhost:9001 responds
[ ] Node dependencies installed
[ ] TypeScript builds successfully
[ ] dist/index.js exists
[ ] MCP configuration exists
[ ] Existing MCP configuration was preserved
[ ] CAPCUT_API_URL is correct
[ ] MCP starts without errors
[ ] Antigravity detects the MCP
[ ] All available CapCut tools are visible
[ ] A test draft can be created
[ ] A test draft can be saved
[ ] No unrelated files/projects were modified

## FINAL RESPONSE

When everything is working, give me a concise report containing:

1. Installation locations
2. VectCutAPI status
3. MCP status
4. MCP configuration path
5. CapCut installation path, if detected
6. List of discovered CapCut MCP tools
7. Test results
8. Commands/scripts I can use to start the stack later
9. Any remaining limitation

Do not merely tell me how to install it.

Actually perform the installation, configuration, testing, and troubleshooting first.