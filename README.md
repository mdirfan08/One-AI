One AI — Universal AI Orchestration Platform

One Interface. Multiple AI Capabilities. One Coordinated Workflow.

Video Demo

https://youtu.be/pqWNYYPSYes?si=BBcK5iM6natcMkSz

Project Description

One AI is a universal AI orchestration platform designed to coordinate multiple artificial intelligence capabilities through a single interface. Instead of manually managing separate AI tools for different tasks, users can describe a goal in plain language and let One AI organize the work into a structured workflow.

The platform converts a user's request into a task graph, creates prompts for individual tasks, routes them to configured AI providers, passes results between dependent tasks, evaluates outputs against acceptance criteria, and retries failed tasks within predefined limits. It also supports approval checkpoints, manual handoffs, task history, and configurable spending, token, and call limits.

The project was developed to explore how multiple AI capabilities can be coordinated into a more organized, controlled, and transparent workflow.

Motivation

AI tools can be useful for writing, coding, planning, and content creation, but managing different tools and combining their outputs can become complicated. One AI addresses this problem by providing a unified interface for planning and executing multi-step tasks.

The project also explores important engineering considerations for AI applications, including provider integration, workflow dependencies, error handling, budget controls, security, and honest reporting of system limitations.

Main Features

- Task planning: Converts natural-language requests into structured task graphs.
- Workflow orchestration: Manages dependencies and execution order between tasks.
- Multiple AI providers: Supports OpenAI, Anthropic Claude, and Google Gemini through their respective APIs and SDKs.
- Demo mode: Provides a built-in simulated provider for exploring workflows without API keys.
- Prompt generation: Creates task-specific prompts for individual workflow steps.
- Output evaluation: Checks generated results against acceptance criteria.
- Bounded retries: Retries unsuccessful tasks within configured limits.
- Human approval: Supports approval checkpoints for consequential steps.
- Budget controls: Applies configurable token, call, spending, task, and execution-time limits.
- Code generation workflow: Supports writing project files to a controlled workspace, optional build and test execution, and downloadable artifacts.
- Task history and artifacts: Maintains workflow results and generated outputs.
- Security controls: Includes API-key redaction, path validation, restricted file types, and sandboxed static previews.

Technologies Used

Backend

- Python
- FastAPI
- Pydantic
- SQLite
- Pytest
- Official AI provider SDKs

Frontend

- React
- TypeScript
- Vite
- Vitest

Deployment and Development Tools

- GitHub for source-code hosting
- Render for deployment
- Node.js and npm
- Uvicorn for running the backend

System Architecture

One AI follows a modular architecture consisting of the following components:

1. Planner: Interprets a user request and generates a structured workflow.
2. Task Graph Validator: Validates task dependencies and checks for cycles.
3. Prompt Builder: Creates prompts tailored to individual tasks.
4. Router: Selects eligible providers based on task requirements and configuration.
5. Executor: Runs ready tasks, manages dependencies, and coordinates provider calls.
6. Budget Manager: Checks configured token, call, and spending limits before provider requests.
7. Evaluator: Checks task results against acceptance criteria.
8. Retry and Approval Manager: Handles bounded retries and human approval checkpoints.
9. Workspace and Artifact Manager: Stores generated files and makes supported artifacts available to users.
10. Frontend Interface: Allows users to create tasks, review plans, approve workflows, monitor progress, and access results.

Workflow

The typical workflow is:

1. The user describes a task in plain language.
2. One AI generates and validates a task graph.
3. The user reviews and approves the plan.
4. The platform routes tasks to configured AI providers.
5. Outputs are passed between dependent tasks.
6. Results are evaluated against acceptance criteria.
7. Failed tasks may be retried within configured limits.
8. The workflow pauses when approval or manual input is required.
9. The platform records the final status and available artifacts.

Installation and Setup

Prerequisites

- Python 3.11 or later
- Node.js 20 or later
- npm

Backend Setup

Open a terminal in the project directory and run:

cd backend
python -m venv .venv

Activate the virtual environment.

On Windows:

.venv\Scripts\Activate.ps1

On macOS or Linux:

source .venv/bin/activate

Install the dependencies:

pip install -r requirements-dev.txt

Optionally create a local environment file:

cp .env.example .env

On Windows Command Prompt, use:

copy .env.example .env

Start the backend:

uvicorn app.main:app --host 127.0.0.1 --port 8000

Frontend Setup

Open a second terminal from the project root:

cd frontend
npm install
npm run dev

Open the local address displayed by Vite, normally "http://localhost:5173".

For a no-key demonstration, select Demo mode and use the example workflow. Demo outputs are simulated and should not be interpreted as real AI-generated results.

Connecting AI Providers

One AI supports OpenAI, Anthropic Claude, and Google Gemini through their APIs.

Users can configure provider API keys through environment variables or the application's provider settings. API access may incur charges or be subject to provider-specific quotas and free-tier limits.

A chat subscription does not automatically include API usage. API keys should be kept private and must never be committed to the repository.

Testing

The project includes backend tests for components such as workflow validation, routing, budget enforcement, retries, approvals, workspace safety, artifact export, and demo execution. Mock providers and fake SDK clients are used for offline testing.

Some integration paths, including the full frontend, API layer, and live provider requests, have not been fully verified end to end. Therefore, the existence of tests does not imply that every feature has been tested against live services.

Security and Privacy

- API keys are handled by the backend and are not intended to be exposed in frontend code.
- Sensitive configuration files and local data are excluded from version control through Git ignore rules.
- Workspace operations validate paths and restrict file types.
- Generated-code execution is disabled by default.
- The application uses configurable execution limits and approval checkpoints.
- Prompts and outputs may be sent to the selected AI provider for processing.
- Local project data is not encrypted by default.

The application is primarily designed as a single-user prototype. Public deployment requires additional security measures, including HTTPS and appropriate access controls.

Limitations

- Demo mode uses simulated content and a heuristic planner.
- API quotas and remaining provider credits are controlled by the respective providers.
- Usage estimates and cost reporting depend on available usage data and configured pricing.
- Web-verified research with citations is not implemented.
- Video generation, voiceover generation, and final video assembly require manual handoff.
- Some frontend, API, and live-provider integration paths require additional end-to-end testing.
- Generated-code execution is not a complete security sandbox.

Future Improvements

Potential improvements include:

- End-to-end testing of the frontend, backend, and live provider integrations.
- Stronger automated code validation and testing.
- Container-based sandboxing for generated code.
- Integrated web search with verifiable citations.
- Additional image, video, and text-to-speech providers.
- Streaming workflow progress and improved usage reporting.
- Secure multi-user authentication and encrypted storage.

Conclusion

One AI demonstrates how multiple AI capabilities can be coordinated through a structured workflow with task dependencies, provider routing, output evaluation, approval checkpoints, and configurable resource limits.

The project provides a foundation for further experimentation with AI orchestration while documenting the current implementation, testing status, security considerations, and areas for improvement.
