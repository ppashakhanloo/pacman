#!/usr/bin/env python3.6

import subprocess
import os.path
import sys
import glob

from optparse import OptionParser 

import json

ARCH='x86_64-linux-gnu'
working_dir = ""

# TODO : Stop packages from re-loading meta

# Really just for tracking a bit more about a symbol stored in our table
class Symbol:
    name = ""
    libs = []
    metas = []

    def __init__(self,name,libs,metas):
        self.name = name
        self.libs = libs
        self.metas = metas

class Meta:
    package_name = ""
    package_deb = ""
    has_symbols = False
    shared_libs = []

    def __init__(self,package_name,package_deb,has_symbols,shared_libs):
        self.package_name = package_name
        self.package_deb = package_deb
        self.has_symbols = has_symbols
        self.shared_libs = shared_libs

    def json_state(self):
        return self.__dict__

    def as_meta(dct):
        return Meta(dct["package_name"],dct["package_deb"],dct["has_symbols"],dct["shared_libs"])
    

class MetaEncoder(json.JSONEncoder):

    def default(self,o):
        if isinstance(o, Meta):
            return o.json_state()
        else:
            return json.JSONEncoder.default(self, o)


def read_dependency_list(name):
    deps = {}
    with open(name, 'r') as f:
        for d in f.read().splitlines():
            deps[d] = True
    return deps

def download_deps(deps):
    debs = {}

    for d in deps:
        print('fetching ' + d)
        try:
            out = subprocess.check_output(['apt-get', 'download', d])
            deb = glob.glob(d + '*.deb')[0]
            debs[d] = deb
            os.rename(deb, working_dir + '/' + deb)
        except:
            print("No package found for " + d)

    return debs

def build_symbols(meta):
    try:
        subprocess.check_call(['dpkg', '-x', meta.package_deb, 'tmp']) 
        subprocess.check_call(['dpkg-gensymbols', '-v0', '-p' + meta.package_name, '-etmp/lib/'+ARCH+'/lib*.so*', '-Osymbols'])
        meta.has_symbols = True
        subprocess.check_call(['rm', '-rf', 'tmp'])
    except subprocess.CalledProcessError as err:
        print(err)
        print('failed to build symbols file for ' + meta.package_deb)
    

def extract_debs(debs,metas):
    # Create some metadata about our little repository
    home = os.getcwd()

    for dep,deb in debs.items():
        debhome = os.path.join(working_dir,dep)

        if os.path.exists(debhome):
            continue

        os.mkdir(debhome)
        os.rename(os.path.join(working_dir,deb),os.path.join(debhome,deb))
        os.chdir(debhome)

        print('Extracting ' + deb)
        out = subprocess.check_output(['ar', '-xv', deb])

        if os.path.exists("control.tar.xz"):
            out = subprocess.check_output(['tar', 'xf', 'control.tar.xz'])
        elif os.path.exists("control.tar.gz"):
            out = subprocess.check_output(['tar', '-xzf', 'control.tar.gz'])


        # Test for symbol file
        has_sym = os.path.exists('symbols')
        meta = Meta(dep, deb, has_sym, [])
        if not has_sym:
            print(dep + ' has no symbols file. Attempting to build...')
            build_symbols(meta)

        metas[deb] = meta

        os.chdir(home) 



def parse_symbols(meta,symbols):
    # We'll point every symbol to its metadata for now
    # Build a true repo later

    with open(os.path.join(working_dir, meta.package_name, "symbols")) as f:
        current_lib = ""
        for l in f.readlines():
            toks = l.split()
            if toks[-1] == "#MINVER#":
                current_lib = toks[0]
                meta.shared_libs.append(current_lib)
            elif toks[0] == "|":
                pass
            else:
                name = toks[0].split("@")[0]
                if name in symbols:
                    # Possible conflict (really only an issue between packages for now)
                    symbols[name].libs.append(current_lib)
                    symbols[name].metas.append(meta)
                else:    
                    symbols[name] = Symbol(name,[current_lib],[meta])
        

def load_meta():
    metas = {}
    if os.path.exists(os.path.join(working_dir, 'meta.txt')):
        with open(os.path.join(working_dir, 'meta.txt'), 'r') as f:
            for line in f:
                m = json.loads(line, object_hook=Meta.as_meta) 
                metas[m.package_name] = m

    return metas

def save_meta(meta):
    with open(os.path.join(working_dir,'meta.txt'), 'w') as f:
        for k,m in metas.items():
            f.write(json.dumps(m, cls=MetaEncoder) + '\n')

def load_symbols(metas):
    symbols = {}

    for k,m in metas.items():
        if m.has_symbols:
            parse_symbols(m, symbols)

    return symbols

def load_trace(name):
    calls = []
    if os.path.exists(name):
        with open(name, 'r') as f:
            for line in f:
                j = json.loads(line)
                calls.append(j)
    else:
        return {}

    return calls

def check_deps(metas,symbols,calls):
    track = {}
    for k,m in metas.items():
        track[m.package_name] = None

    for c in calls:
        if c["indirect"]:
            continue
        fname = c["fnptr"][1:]
        sym = symbols.get(fname)
        if sym is not None:
            track[sym.metas[0].package_name] = sym.libs[0]
        
    # Just for nice output
    used = []
    notused = []

    for d,t in track.items():
        if t is not None:
            used.append({"package_name":d, "shared_lib":t})
        else:
            notused.append(d)

    print('Package has ' + str(len(track)) + ' tracked dependencies')
    print('Using ' + str(len(used)) + ':')
    for d in used:
        print('\t' + d["package_name"] + ' ===> ' + d["shared_lib"])

    print('Not using ' + str(len(notused)) + ':')
    for d in notused:
        print('\t' + d)


usage = "usage: %prog [options] dependency-list"
parser = OptionParser(usage=usage)
parser.add_option('-d', '--dir', dest='working_dir', default='symbol-out', help='use DIR as working output directory', metavar='DIR')
parser.add_option('-t', '--trace', dest='trace', help='load trace file DIR', metavar='TRACE')
parser.add_option('-l', '--load', action='store_true', help='jump straight to loading the repository symbols')

(options, args) = parser.parse_args()

working_dir = options.working_dir

metas = load_meta()

if len(args) < 1:
    print("error: must supply dependency-list")
    parser.print_usage()
    sys.exit(1)

if not options.load:
    deps=read_dependency_list(args[0])
    debs=download_deps(deps)
    extract_debs(debs,metas)

symbols = load_symbols(metas)
save_meta(metas)

if options.trace is not None:
    calls = load_trace(options.trace)
    check_deps(metas,symbols,calls) 
   








