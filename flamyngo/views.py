import json
import re
import os

from pymongo import MongoClient

from monty.serialization import loadfn
from monty.json import jsanitize

from flask import render_template, make_response
from flask.json import jsonify

from flamyngo.app import app

from functools import wraps
from flask import request, Response

module_path = os.path.dirname(os.path.abspath(__file__))


SETTINGS = loadfn(os.environ["FLAMYNGO"])
CONN = MongoClient(SETTINGS["db"]["host"], SETTINGS["db"]["port"],
                   connect=False)
DB = CONN[SETTINGS["db"]["database"]]
if "username" in SETTINGS["db"]:
    DB.authenticate(SETTINGS["db"]["username"], SETTINGS["db"]["password"])
CNAMES = [d["name"] for d in SETTINGS["collections"]]
CSETTINGS = {d["name"]: d for d in SETTINGS["collections"]}
AUTH_USER = SETTINGS.get("AUTH_USER", None)
AUTH_PASSWD = SETTINGS.get("AUTH_PASSWD", None)


def check_auth(username, password):
    """
    This function is called to check if a username /
    password combination is valid.
    """
    if AUTH_USER is None:
        return True
    return username == AUTH_USER and password == AUTH_PASSWD


def authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response(
        'Could not verify your access level for that URL. You have to login '
        'with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if (AUTH_USER is not None) and (not auth or not check_auth(
                auth.username, auth.password)):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


@app.route('/', methods=['GET'])
@requires_auth
def index():
    return make_response(render_template('index.html', collections=CNAMES))


def _get_val(k, d, processing_func):
    toks = k.split(".")
    try:
        val = d[toks[0]]
        for t in toks[1:]:
            try:
                val = val[t]
            except KeyError:
                # Handle integer indices
                val = val[int(t)]
        val = process(val, processing_func)
    except Exception as ex:
        print(str(ex))
        # Return the base value if we can descend into the data.
        val = None
    return val


@app.route('/query', methods=['GET'])
@requires_auth
def query():
    cname = request.args.get("collection")
    settings = CSETTINGS[cname]
    search_string = request.args.get("search_string")
    projection = [t[0] for t in settings["summary"]]

    if search_string.strip() != "":
        criteria = {}
        for regex in settings["query"]:
            if re.match(r'%s' % regex[1], search_string):
                criteria[regex[0]] = process(search_string, regex[2])
                break
        if not criteria:
            criteria = json.loads(search_string)
        results = []
        for r in DB[cname].find(criteria, projection=projection):
            processed = {}
            fields = []
            for m in settings["summary"]:
                if len(m) == 2:
                    k, v = m
                    mapped_k = k
                elif len(m) == 3:
                    k, v, mapped_k = m
                else:
                    raise ValueError("Invalid summary settings!")
                val = _get_val(k, r, v.strip())
                processed[mapped_k] = val
                fields.append(mapped_k)
            results.append(processed)
        error_message = None
    else:
        results = []
        error_message = "No results!"
    return make_response(render_template(
        'index.html', collection_name=cname,
        results=results, fields=fields, search_string=search_string,
        unique_key=settings["unique_key"],
        active_collection=cname,
        collections=CNAMES,
        error_message=error_message)
    )


@app.route('/plot', methods=['GET'])
@requires_auth
def plot():
    cname = request.args.get("collection")
    if not cname:
        return make_response(render_template('plot.html', collections=CNAMES))
    settings = CSETTINGS[cname]
    plot_type = request.args.get("plot_type")
    search_string = request.args.get("search_string")
    xaxis = request.args.get("xaxis")
    yaxis = request.args.get("yaxis")

    projection = [xaxis, yaxis]

    if search_string.strip() != "":
        criteria = {}
        for regex in settings["query"]:
            if re.match(r'%s' % regex[1], search_string):
                criteria[regex[0]] = process(search_string, regex[2])
                break
        if not criteria:
            criteria = json.loads(search_string)
        xdata = []
        ydata = []
        for r in DB[cname].find(criteria, projection=projection):
            x = _get_val(xaxis, r, "str")
            y = _get_val(yaxis, r, "str")
            try:
                if x == int(x):
                    x = int(x)
            except:
                try:
                    x = float(x)
                except:
                    # X is string.
                    pass
            try:
                if y == int(y):
                    y = int(y)
            except:
                try:
                    y = float(y)
                except:
                    # Y is string.
                    pass
            if x and y:
                xdata.append(x)
                ydata.append(y)
        error_message = None
    else:
        xdata = []
        ydata = []
        error_message = "No results!"
    try:
        if xdata and ydata:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import importlib
            color_cycle = ("qualitative", "Set1_9")
            mod = importlib.import_module("palettable.colorbrewer.%s" %
                                          color_cycle[0])
            colors = getattr(mod, color_cycle[1]).mpl_colors
            from cycler import cycler

            plt.figure(figsize=(8, 6), facecolor="w")
            ax = plt.gca()
            ax.set_prop_cycle(cycler('color', colors))
            plt.xticks(fontsize=16)
            plt.yticks(fontsize=16)
            ax = plt.gca()
            ax.set_title(ax.get_title(), size=28)
            ax.set_xlabel(ax.get_xlabel(), size=20)
            ax.set_ylabel(ax.get_ylabel(), size=20)
            if plot_type == "scatter":
                plt.plot(xdata, ydata, "o")
            else:
                values = list(range(len(xdata)))
                plt.bar(values, ydata)
                plt.xticks(values, xdata, rotation=-90)
            plt.xlabel(xaxis)
            plt.ylabel(yaxis)
            plt.tight_layout()
            from io import StringIO
            s = StringIO()
            plt.savefig(s, format="svg")

            svg = s.getvalue()
            s.close()
        else:
            svg = None
    except Exception as ex:
        error_message = str(ex)
        svg = None

    return make_response(render_template(
        'plot.html', collection_name=cname,
        plot=svg, search_string=search_string,
        xaxis=xaxis, yaxis=yaxis,
        active_collection=cname,
        collections=CNAMES,
        error_message=error_message)
    )


@app.route('/<string:collection_name>/doc/<string:uid>')
@requires_auth
def get_doc(collection_name, uid):
    settings = CSETTINGS[collection_name]
    criteria = {
        settings["unique_key"]: process(uid, settings["unique_key_type"])}
    doc = DB[collection_name].find_one(criteria)
    return make_response(render_template(
        'doc.html', collection_name=collection_name, doc_id=uid)
    )


@app.route('/<string:collection_name>/doc/<string:uid>/json')
@requires_auth
def get_doc_json(collection_name, uid):
    settings = CSETTINGS[collection_name]
    criteria = {
        settings["unique_key"]: process(uid, settings["unique_key_type"])}
    doc = DB[collection_name].find_one(criteria)
    return jsonify(jsanitize(doc))


def process(val, vtype):
    toks = vtype.rsplit(".", 1)
    if len(toks) == 1:
        func = globals()["__builtins__"][toks[0]]
    else:
        mod = __import__(toks[0], globals(), locals(), [toks[1]], 0)
        func = getattr(mod, toks[1])
    return func(val)


if __name__ == "__main__":
    app.run(debug=True)
