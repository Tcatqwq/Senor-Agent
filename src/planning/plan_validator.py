import ast
import json
import os

import networkx as nx
import yaml

from src.utils import print_tips


def _tool_files_dir(config=None):
    from src.llm_virtual_env.catalog import generated_ready, tool_files_dir, use_generated_catalog

    if use_generated_catalog() and not generated_ready():
        raise FileNotFoundError(
            "虚拟模态已开启，但尚未生成 vocabulary/grammar。请在「资源浏览-LLM虚拟环境」中保存配置。"
        )
    return str(tool_files_dir())


def coerce_query_text_arg(text):
    '''Parse LLM-emitted query ranges like "[[20231116, 0, 24]]" into a list of [date, start, end].'''
    parsed = text
    for _ in range(3):
        if not isinstance(parsed, str):
            break
        stripped = parsed.strip()
        if not stripped:
            return text
        try:
            parsed = json.loads(stripped)
            continue
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(stripped)
            except (ValueError, SyntaxError):
                return text

    if not isinstance(parsed, list) or len(parsed) == 0:
        return text

    if not isinstance(parsed[0], (list, tuple)):
        parsed = [parsed]

    coerced = []
    for item in parsed:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            coerced.append([int(item[0]), int(item[1]), int(item[2])])

    return coerced if coerced else text


def normalize_plan(plan):
    '''Coerce stringified tool args so execution can index time ranges as lists.'''
    if not isinstance(plan, list):
        return plan

    for tool in plan:
        if "id" in tool:
            tool["id"] = int(tool["id"])
        if "dep" in tool:
            tool["dep"] = [int(dep) for dep in (tool.get("dep") or [-1])]
        args = tool.get("args")
        if isinstance(args, dict) and "text" in args:
            args["text"] = coerce_query_text_arg(args["text"])

    return plan


# check whether the plan's nodes are correct
def check_node(plan):
    '''
    check whether there is error node in the plan

    Args:
        plan (list): the plan to be checked

    Returns:
        bool: True if there is no error node in the plan, False otherwise
    '''
    
    tool_name_id_mapping_path = os.path.join(_tool_files_dir(), "tool_name_id_mapping.json")
    tool_name_id_mapping = json.load(open(tool_name_id_mapping_path))
    
    # node correct flag
    node_correct = True
    
    # node error collection
    node_error = []
    
    # check whether the tools in the plan are in the tool name to integer id mapping, if not, add them into the node error collection
    for tool in plan:
        if tool["tool_name"] not in tool_name_id_mapping:
            node_correct = False
            node_error.append(tool["tool_name"])
    
    return node_correct, node_error

# check whether the plan's edges are correct
def check_edge(G_rules, G_plan):
    '''
    This function checks whether the inputted plan's edges are correct.

    Args:
        plan_rules (nx.DiGraph): the graph of the grammar rules
        plan (nx.DiGraph): the graph of the plan

    Returns:
        bool: True if the plan's edges are correct, False otherwise
    '''
    
    tool_name_id_mapping_path = os.path.join(_tool_files_dir(), "tool_name_id_mapping.json")
    tool_name_id_mapping = json.load(open(tool_name_id_mapping_path))
    tool_id_name_mapping = dict(zip(tool_name_id_mapping.values(), tool_name_id_mapping.keys()))
    
    # print(tool_id_name_mapping)
    # print(tool_name_id_mapping)
    
    # edge correct flag
    edge_correct = True
    
    # edge error collection
    edge_errors= []
    
    # wrong nodes
    wrong_nodes = []
    
    # check whether the edges in the plan are in the grammar rules, if not, add them into the edge error collection
    
    # print("====================================")
    # print("G_plan.edges: ", G_plan.edges)
    # print("G_rules.edges: ", G_rules.edges)
    # print("====================================")
    
    if not all(edge in G_rules.edges for edge in G_plan.edges):
        edge_correct = False
        
        for edge in G_plan.edges:
            if edge not in G_rules.edges:
                edge_errors.append(tool_id_name_mapping[int(edge[0])]+" -> "+tool_id_name_mapping[int(edge[1])])
                # print(edge)
                # print(G_plan.nodes[edge[1]])
                wrong_nodes.append(G_plan.nodes[edge[1]]["id_in_plan"])
    
    return edge_correct, edge_errors, wrong_nodes

# check whether the plan's grammar is correct
def check_grammar_rules(plan):
    '''
    This function checks whether the inputted plan's grammar is correct.

    Args:
        plan (int): the plan to be checked

    Returns:
        dict: the checking result
    '''
    
    tool_dir = _tool_files_dir()
    tool_name_id_mapping_path = os.path.join(tool_dir, "tool_name_id_mapping.json")
    tool_name_id_mapping = json.load(open(tool_name_id_mapping_path))
    
    # create an empty directed graph to store the plan
    G_plan = nx.DiGraph()
    
    # read the global grammar rules
    grammar_rules_path = os.path.join(tool_dir, "grammar_rules.graphml")
    G_rules = nx.read_graphml(grammar_rules_path)
    
    # initialize the checking result
    checking_result = {
        "pass": None,
        "error": None
    }
    
    # check whether there is error node in the plan
    node_correct, node_error = check_node(plan)
    
    if not node_correct:
        checking_result["pass"] = False
        checking_result["error"] = "The following nodes are not in the grammar rules: "+", ".join(node_error)
        return checking_result
    
    # construct the plan graph
    for tool in plan:
        G_plan.add_node(str(tool_name_id_mapping[tool["tool_name"]]), id_in_plan=tool["id"])
        
        for dep in tool["dep"]:
            dep_id = int(dep)
            if dep_id >= 0:
                G_plan.add_edge(
                    str(tool_name_id_mapping[plan[dep_id]["tool_name"]]),
                    str(tool_name_id_mapping[tool["tool_name"]])
                )
            else:
                G_plan.add_edge(
                    str(-1),
                    str(tool_name_id_mapping[tool["tool_name"]])
                )
    
    edge_correct, edge_errors, wrong_nodes = check_edge(G_rules, G_plan)
    
    if edge_correct:
        checking_result["pass"] = True
        # print("[√] The plan's grammar is correct.")
        print_tips("The plan's grammar is correct.", text_color="yellow", emoji="white_check_mark", border=False)
    else:
        checking_result["pass"] = False
        checking_result["error"] = "The following edges are not in the grammar rules: "+str(edge_errors)+". The following nodes are not in the grammar rules: " +str(wrong_nodes)
        
    return checking_result

def _report_check(report, check, status, **details):
    if report is not None:
        report(check, status, **details)


# check whether the plan's format is correct
def check_format(plan_str, report=None):
    '''
    This function checks whether the inputted plan string's format is correct.

    Args:
        plan_str (str): the plan in string form to be checked
        
    Returns:
        dict: the checking result
        plan_in_json: the plan in json form
    '''
    
    # if the plan string contains the word "error"
    if "error" in plan_str:
        _report_check(report, "model_error", "failed")
        return {
            "pass": False,
            "error": "The plan contains the word 'error'."
        }, None
    else:
        print_tips("There is no 'error' word in the response.", text_color="yellow", emoji="white_check_mark", border=False)
        _report_check(report, "model_error", "passed")
    
    # Remove leading and trailing ' ' and '\n' characters
    plan_str = plan_str.strip()
    
    # try to load the plan string into a json object
    try:
        plan = json.loads(plan_str)
    except json.JSONDecodeError:
        _report_check(report, "json", "failed")
        return {
            "pass": False,
            "error": "The plan is not in json format."
        }, None
    print_tips("The plan is in json format.", text_color="yellow", emoji="white_check_mark", border=False)
    _report_check(report, "json", "passed")

    if not isinstance(plan, list):
        _report_check(report, "tool_list", "failed")
        return {"pass": False, "error": "The plan must be a JSON list of tools."}, None
        
    # check whether the plan is empty
    if len(plan) == 0:
        _report_check(report, "nonempty", "empty")
        return {
            "pass": True,
            "error": None
        }, []
    else:
        print_tips("The plan is not empty.", text_color="yellow", emoji="white_check_mark", border=False)
        _report_check(report, "nonempty", "passed")
    
    # check if the arttributes of each tool are correct
    for index, tool in enumerate(plan):
        if not isinstance(tool, dict):
            _report_check(report, "tool_list", "failed")
            return {"pass": False, "error": "The plan must be a JSON list of tool objects."}, None
        missing = next((field for field in ("args", "dep", "id", "tool_name") if field not in tool), None)
        name = str(tool.get("tool_name", ""))
        if missing:
            _report_check(report, "tool_fields", "failed", tool=name, index=index, missing=missing)
            return {"pass": False, "error": f"The tool does not have the '{missing}' attribute."}, None
        print_tips(f"The tool {name} has all the necessary attributes.", text_color="yellow", emoji="white_check_mark", border=False)
        _report_check(report, "tool_fields", "passed", tool=name, index=index)
    
    return {
        "pass": True,
        "error": None
    }, plan
    
# check whether the plan is correct
def check_plan(plan_str, report=None):
    '''
    This function checks whether the inputted plan is correct.

    Args:
        plan_str (str): the plan to be checked

    Returns:
        dict: the checking result
    '''
    
    # check whether the plan's format is correct
    format_check_result, plan = check_format(plan_str, report=report)
    
    if plan == []:
        return {
            "pass": True,
            "error": None
        }
    
    if not format_check_result["pass"]:
        return format_check_result

    try:
        plan = normalize_plan(plan)
    except (TypeError, ValueError) as exc:
        _report_check(report, "dependency_ids", "failed")
        return {
            "pass": False,
            "error": f"Plan id/dep must be integers: {exc}",
        }

    # check whether the plan's grammar is correct
    grammar_check_result = check_grammar_rules(plan)
    _report_check(report, "grammar", "passed" if grammar_check_result["pass"] else "failed")
    
    if not grammar_check_result["pass"]:
        return grammar_check_result
    
    return {
        "pass": True,
        "error": None
    }

