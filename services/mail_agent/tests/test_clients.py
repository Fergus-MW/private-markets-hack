import os
import unittest
from unittest.mock import Mock, patch

from mail_agent.clients import GraphClient, route


class ClientTests(unittest.TestCase):
    PROJECT = "a" * 64

    def response(self, name, args):
        response = Mock()
        response.json.return_value = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"functionCall": {"name": name, "args": args}}]}}]}
        return response

    def routed(self, name, args):
        projects = [{"key": self.PROJECT, "name": "Fund A"}]
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test", "GEMINI_MODEL": "gemini-3.1-pro-preview"}), \
                patch("mail_agent.clients.httpx.post", return_value=self.response(name, args)):
            return route("request", projects)

    def test_project_question_and_graph_links_are_validated_tool_calls(self):
        self.assertEqual(self.routed("answer_project_question", {
            "project_id": self.PROJECT, "question": "Who manages the fund?"})["tool_call"]["name"],
            "answer_project_question")
        self.assertEqual(self.routed("get_project_graph_link", {
            "project_id": self.PROJECT})["tool_call"]["name"], "get_project_graph_link")
        self.assertEqual(self.routed("get_workspace_graph_link", {})["tool_call"]["name"],
                         "get_workspace_graph_link")
        self.assertEqual(self.routed("get_qc_dashboard_link", {
            "project_id": self.PROJECT})["tool_call"]["name"], "get_qc_dashboard_link")

    def test_workflow_status_requires_an_owned_project_and_exact_task_id(self):
        call = self.routed("check_workflow_status", {
            "project_id": self.PROJECT, "job_id": "b" * 64, "verbose": True})["tool_call"]
        self.assertEqual(call["name"], "check_workflow_status")
        self.assertTrue(call["args"]["verbose"])
        with self.assertRaises(ValueError):
            self.routed("check_workflow_status", {"project_id": self.PROJECT, "job_id": "not-a-task"})
        with self.assertRaises(ValueError):
            self.routed("check_workflow_status", {"project_id": "c" * 64, "job_id": "b" * 64})

    def test_project_tools_cannot_select_another_accounts_project(self):
        with self.assertRaises(ValueError):
            self.routed("get_project_graph_link", {"project_id": "b" * 64})
        with self.assertRaises(ValueError):
            self.routed("get_qc_dashboard_link", {"project_id": "b" * 64})
        with self.assertRaises(ValueError):
            self.routed("answer_project_question", {"project_id": "b" * 64, "question": "Tell me"})

    def test_visualization_links_are_private_frontend_routes(self):
        with patch.dict(os.environ, {"FRONTEND_PUBLIC_ORIGIN": "https://frontend.example/"}):
            self.assertEqual(GraphClient.visualization(), "https://frontend.example/graphs/workspace")
            self.assertEqual(GraphClient.visualization(self.PROJECT),
                             "https://frontend.example/graphs/" + self.PROJECT)
            with self.assertRaises(ValueError):
                GraphClient.visualization("../other")
            self.assertEqual(GraphClient.dashboard(self.PROJECT),
                             "https://frontend.example/dashboard/" + self.PROJECT)
            with self.assertRaises(ValueError):
                GraphClient.dashboard("../other")
        with patch.dict(os.environ, {"FRONTEND_PUBLIC_ORIGIN": "http://frontend.example"}):
            with self.assertRaises(ValueError):
                GraphClient.dashboard(self.PROJECT)

    def test_production_model_calls_use_the_gateway(self):
        projects = [{"key": self.PROJECT, "name": "Fund A"}]
        response = self.response("get_project_graph_link", {"project_id": self.PROJECT})
        with patch.dict(os.environ, {"MODEL_GATEWAY_URL": "https://gateway.example",
                                    "GEMINI_API_KEY": "must-not-be-used"}), \
                patch("mail_agent.clients.fetch_id_token", return_value="identity"), \
                patch("mail_agent.clients.httpx.post", return_value=response) as post:
            route("show graph", projects)
        self.assertEqual(post.call_args.args[0], "https://gateway.example/v1/generate")
        self.assertEqual(post.call_args.kwargs["json"]["cache_namespace"], "mail-router-v1")
        self.assertIn("systemInstruction", post.call_args.kwargs["json"]["request"])


if __name__ == "__main__":
    unittest.main()
