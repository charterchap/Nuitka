#     Copyright 2015, Kay Hayen, mailto:kay.hayen@gmail.com
#
#     Part of "Nuitka", an optimizing Python compiler that is compatible and
#     integrates with CPython, but also works on its own.
#
#     Licensed under the Apache License, Version 2.0 (the "License");
#     you may not use this file except in compliance with the License.
#     You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.
#
""" Assignment related nodes.

All kinds of assignment targets as well as the assignment statement and
expression are located here. These are the core of value control flow.

Note: Currently there is also assignment to keeper nodes in KeeperNodes,
that should be unified at some point.

"""

from nuitka import Options, VariableRegistry
from nuitka.utils import Utils

from .NodeBases import NodeBase, StatementChildrenHavingBase
from .NodeMakingHelpers import (
    makeStatementExpressionOnlyReplacementNode,
    makeStatementOnlyNodesFromExpressions,
    makeStatementsSequenceReplacementNode
)


class StatementAssignmentVariable(StatementChildrenHavingBase):
    kind = "STATEMENT_ASSIGNMENT_VARIABLE"

    named_children = (
        "source",
        "variable_ref"
    )

    inplace_suspect = None

    def __init__(self, variable_ref, source, source_ref):
        assert variable_ref is not None, source_ref
        assert source is not None, source_ref

        assert variable_ref.isTargetVariableRef()

        StatementChildrenHavingBase.__init__(
            self,
            values     = {
                "source"       : source,
                "variable_ref" : variable_ref
            },
            source_ref = source_ref
        )

        self.variable_trace = None

    def getDetail(self):
        variable_ref = self.getTargetVariableRef()
        variable = variable_ref.getVariable()

        if variable is not None:
            return "to variable %s" % variable
        else:
            return "to variable %s" % self.getTargetVariableRef()

    getTargetVariableRef = StatementChildrenHavingBase.childGetter(
        "variable_ref"
    )
    getAssignSource = StatementChildrenHavingBase.childGetter(
        "source"
    )
    setAssignSource = StatementChildrenHavingBase.childSetter(
        "source"
    )

    def markAsInplaceSuspect(self):
        self.inplace_suspect = True

    def isInplaceSuspect(self):
        return self.inplace_suspect

    def mayRaiseException(self, exception_type):
        return self.getAssignSource().mayRaiseException(exception_type)

    def computeStatement(self, constraint_collection):
        # This is very complex stuff, pylint: disable=R0912

        # TODO: Way too ugly to have global trace kinds just here, and needs to
        # be abstracted somehow. But for now we let it live here: pylint: disable=R0911,R0915

        # Assignment source may re-compute here:
        constraint_collection.onExpression(self.getAssignSource())
        source = self.getAssignSource()

        # No assignment will occur, if the assignment source raises, so strip it
        # away.
        if source.willRaiseException(BaseException):

            result = makeStatementExpressionOnlyReplacementNode(
                expression = source,
                node       = self
            )

            return result, "new_raise", """\
Assignment raises exception in assigned value, removed assignment."""

        variable_ref = self.getTargetVariableRef()
        variable = variable_ref.getVariable()

        # Not allowed anymore at this point.
        assert variable is not None

        # Assigning from and to the same variable, can be optimized away
        # immediately, there is no point in doing it. Exceptions are of course
        # module variables that collide with built-in names.
        if not variable.isModuleVariable() and \
             source.isExpressionVariableRef() and \
             source.getVariable() == variable:

            # A variable access that has a side effect, must be preserved,
            # so it can e.g. raise an exception, otherwise we can be fully
            # removed.
            if source.mayHaveSideEffects():
                result = makeStatementExpressionOnlyReplacementNode(
                    expression = source,
                    node       = self
                )

                return result, "new_statements", """\
Reduced assignment of variable from itself to access of it."""
            else:
                return None, "new_statements", """\
Removed assignment of variable from itself which is known to be defined."""


        # If the assignment source has side effects, we can simply evaluate them
        # beforehand, we have already visited and evaluated them before.
        if source.isExpressionSideEffects():
            statements = [
                makeStatementExpressionOnlyReplacementNode(
                    side_effect,
                    self
                )
                for side_effect in
                source.getSideEffects()
            ]

            statements.append(self)

            parent = self.parent
            result = makeStatementsSequenceReplacementNode(
                statements = statements,
                node       = self,
            )
            result.parent = parent

            # Need to update it.
            self.setAssignSource(source.getExpression())
            source = self.getAssignSource()

            return result, "new_statements", """\
Side effects of assignments promoted to statements."""

        # Set-up the trace to the trace collection, so future references will
        # find this assignment.
        self.variable_trace = constraint_collection.onVariableSet(
            assign_node = self
        )

        global_trace = VariableRegistry.getGlobalVariableTrace(variable)

        if global_trace is not None and Options.isExperimental():
            last_trace = global_trace.getMatchingAssignTrace(self)

            if last_trace is not None:
                if variable.isLocalVariable() or variable.isTempVariable():
                    if source.isCompileTimeConstant():

                        # Can safely forward propagate only non-mutable constants.
                        if not source.isMutable():
                            provider = self.getParentVariableProvider()

                            if variable.isTempVariable() or \
                               (not provider.isUnoptimized() and \
                                not provider.isClassDictCreation()):

                                if last_trace.hasDefiniteUsages():
                                    self.variable_trace.setReplacementNode(
                                        lambda usage : source.makeClone()
                                    )
                                    propagated = True
                                else:
                                    propagated = False

                                if not last_trace.hasPotentialUsages() and not last_trace.hasNameUsages():
                                    # This limitation may fall later.
                                    if not variable.isSharedLogically():

                                        if not last_trace.getPrevious().isUninitTrace():
                                            # TODO: We could well decide, if that's even necessary.
                                            result = StatementDelVariable(
                                                variable_ref = self.getTargetVariableRef(),
                                                tolerant     = True,
                                                source_ref   = self.getSourceReference()
                                            )
                                        else:
                                            result = None

                                        return (
                                            result,
                                            "new_statements",
                                            "Dropped %s assignment statement to '%s'." % (
                                               "propagated" if propagated else "dead",
                                               self.getTargetVariableRef().getVariableName()
                                            )
                                        )
                        else:
                            # Something might be possible still.

                            pass
                    elif Options.isExperimental() and \
                        source.isExpressionFunctionCreation() and \
                        not source.getFunctionRef().getFunctionBody().isGenerator() and \
                        not source.getFunctionRef().getFunctionBody().isClassDictCreation() and \
                        not source.getDefaults() and  \
                        not source.getKwDefaults() and \
                        not source.getAnnotations():
                        # TODO: These are very mutable, right?

                        provider = self.getParentVariableProvider()

                        if variable.isTempVariable() or \
                           (not provider.isUnoptimized() and \
                            not provider.isClassDictCreation()):

                            # This limitation may fall later.
                            if not variable.isSharedLogically():

                                if last_trace.getDefiniteUsages() <= 1 and \
                                   not last_trace.hasPotentialUsages() and \
                                   not last_trace.hasNameUsages():

                                    if last_trace.getDefiniteUsages() == 1:
                                        self.variable_trace.setReplacementNode(
                                            lambda usage : source.makeClone()
                                        )
                                        propagated = True
                                    else:
                                        propagated = False

                                    if not last_trace.getPrevious().isUninitTrace():
                                        # TODO: We could well decide, if that's even necessary.
                                        result = StatementDelVariable(
                                            variable_ref = self.getTargetVariableRef(),
                                            tolerant     = True,
                                            source_ref   = self.getSourceReference()
                                        )
                                    else:
                                        result = None

                                    return (
                                        result,
                                        "new_statements",
                                        "Dropped %s assignment statement to '%s'." % (
                                           "propagated" if propagated else "dead",
                                           self.getTargetVariableRef().getVariableName()
                                        )
                                    )

                    else:
                        # More cases thinkable.
                        pass

        return self, None, None

    def needsReleaseValue(self):
        previous = self.variable_trace.getPrevious()

        if previous.mustNotHaveValue():
            return False
        elif previous.mustHaveValue():
            return True
        else:
            return None


class StatementAssignmentAttribute(StatementChildrenHavingBase):
    kind = "STATEMENT_ASSIGNMENT_ATTRIBUTE"

    named_children = (
        "source",
        "expression"
    )

    def __init__(self, expression, attribute_name, source, source_ref):
        StatementChildrenHavingBase.__init__(
            self,
            values     = {
                "expression" : expression,
                "source"     : source,
            },
            source_ref = source_ref
        )

        self.attribute_name = attribute_name

    def getDetails(self):
        return {
            "attribute_name" : self.attribute_name
        }

    def getDetail(self):
        return "to attribute %s" % self.attribute_name

    def getAttributeName(self):
        return self.attribute_name

    def setAttributeName(self, attribute_name):
        self.attribute_name = attribute_name

    getLookupSource = StatementChildrenHavingBase.childGetter("expression")
    getAssignSource = StatementChildrenHavingBase.childGetter("source")

    def computeStatement(self, constraint_collection):
        constraint_collection.onExpression(self.getAssignSource())
        source = self.getAssignSource()

        # No assignment will occur, if the assignment source raises, so strip it
        # away.
        if source.willRaiseException(BaseException):
            result = makeStatementExpressionOnlyReplacementNode(
                expression = source,
                node       = self
            )

            return result, "new_raise", """\
Attribute assignment raises exception in assigned value, removed assignment."""

        constraint_collection.onExpression(self.getLookupSource())
        lookup_source = self.getLookupSource()

        if lookup_source.willRaiseException(BaseException):
            result = makeStatementOnlyNodesFromExpressions(
                expressions = (
                    source,
                    lookup_source
                )
            )

            return result, "new_raise", """\
Attribute assignment raises exception in source, removed assignment."""

        # Any code could be run, note that.
        constraint_collection.onControlFlowEscape(self)

        return self, None, None


class StatementAssignmentSubscript(StatementChildrenHavingBase):
    kind = "STATEMENT_ASSIGNMENT_SUBSCRIPT"

    named_children = (
        "source",
        "expression",
        "subscript"
    )

    def __init__(self, expression, subscript, source, source_ref):
        StatementChildrenHavingBase.__init__(
            self,
            values     = {
                "source"     : source,
                "expression" : expression,
                "subscript"  : subscript
            },
            source_ref = source_ref
        )

    getSubscribed = StatementChildrenHavingBase.childGetter("expression")
    getSubscript = StatementChildrenHavingBase.childGetter("subscript")
    getAssignSource = StatementChildrenHavingBase.childGetter("source")

    def computeStatement(self, constraint_collection):
        constraint_collection.onExpression(
            expression = self.getAssignSource()
        )
        source = self.getAssignSource()

        # No assignment will occur, if the assignment source raises, so strip it
        # away.
        if source.willRaiseException(BaseException):
            result = makeStatementExpressionOnlyReplacementNode(
                expression = source,
                node       = self
            )

            return result, "new_raise", """\
Subscript assignment raises exception in assigned value, removed assignment."""

        constraint_collection.onExpression(self.getSubscribed())
        subscribed = self.getSubscribed()

        if subscribed.willRaiseException(BaseException):
            result = makeStatementOnlyNodesFromExpressions(
                expressions = (
                    source,
                    subscribed
                )
            )

            return result, "new_raise", """\
Subscript assignment raises exception in subscribed, removed assignment."""

        constraint_collection.onExpression(
            self.getSubscript()
        )
        subscript = self.getSubscript()

        if subscript.willRaiseException(BaseException):
            result = makeStatementOnlyNodesFromExpressions(
                expressions = (
                    source,
                    subscribed,
                    subscript
                )
            )

            return result, "new_raise", """
Subscript assignment raises exception in subscript value, removed \
assignment."""

        # Any code could be run, note that.
        constraint_collection.onControlFlowEscape(self)

        return self, None, None


class StatementAssignmentSlice(StatementChildrenHavingBase):
    kind = "STATEMENT_ASSIGNMENT_SLICE"

    named_children = (
        "source",
        "expression",
        "lower",
        "upper"
    )

    def __init__(self, expression, lower, upper, source, source_ref):
        assert Utils.python_version < 300

        StatementChildrenHavingBase.__init__(
            self,
            values     = {
                "source"     : source,
                "expression" : expression,
                "lower"      : lower,
                "upper"      : upper
            },
            source_ref = source_ref
        )

    getLookupSource = StatementChildrenHavingBase.childGetter("expression")
    getLower = StatementChildrenHavingBase.childGetter("lower")
    getUpper = StatementChildrenHavingBase.childGetter("upper")
    getAssignSource = StatementChildrenHavingBase.childGetter("source")

    def computeStatement(self, constraint_collection):
        constraint_collection.onExpression(self.getAssignSource())
        source = self.getAssignSource()

        # No assignment will occur, if the assignment source raises, so strip it
        # away.
        if source.willRaiseException(BaseException):
            result = makeStatementExpressionOnlyReplacementNode(
                expression = source,
                node       = self
            )

            return result, "new_raise", """\
Slice assignment raises exception in assigned value, removed assignment."""

        constraint_collection.onExpression(self.getLookupSource())
        lookup_source = self.getLookupSource()

        if lookup_source.willRaiseException(BaseException):
            result = makeStatementOnlyNodesFromExpressions(
                expressions = (
                    source,
                    lookup_source
                )
            )

            return result, "new_raise", """\
Slice assignment raises exception in sliced value, removed assignment."""

        constraint_collection.onExpression(self.getLower(), allow_none = True)
        lower = self.getLower()

        if lower is not None and lower.willRaiseException(BaseException):
            result = makeStatementOnlyNodesFromExpressions(
                expressions = (
                    source,
                    lookup_source,
                    lower
                )
            )

            return result, "new_raise", """\
Slice assignment raises exception in lower slice boundary value, removed \
assignment."""

        constraint_collection.onExpression(self.getUpper(), allow_none = True)
        upper = self.getUpper()

        if upper is not None and upper.willRaiseException(BaseException):
            result = makeStatementOnlyNodesFromExpressions(
                expressions = (
                    source,
                    lookup_source,
                    lower,
                    upper
                )
            )

            return result, "new_raise", """\
Slice assignment raises exception in upper slice boundary value, removed \
assignment."""

        # Any code could be run, note that.
        constraint_collection.onControlFlowEscape(self)

        return self, None, None


class StatementDelVariable(StatementChildrenHavingBase):
    kind = "STATEMENT_DEL_VARIABLE"

    named_children = (
        "variable_ref",
    )

    def __init__(self, variable_ref, tolerant, source_ref):
        assert variable_ref is not None
        assert variable_ref.isTargetVariableRef()
        assert tolerant is True or tolerant is False

        StatementChildrenHavingBase.__init__(
            self,
            values     = {
                "variable_ref" : variable_ref
            },
            source_ref = source_ref
        )

        self.variable_trace = None
        self.previous_trace = None

        self.tolerant = tolerant

    def getDetail(self):
        variable_ref = self.getTargetVariableRef()
        variable = variable_ref.getVariable()

        if variable is not None:
            return "to variable %s" % variable
        else:
            return "to variable %s" % self.getTargetVariableRef()

    def getDetails(self):
        return {
            "tolerant" : self.tolerant
        }

    # TODO: Value propagation needs to make a difference based on this.
    def isTolerant(self):
        return self.tolerant

    getTargetVariableRef = StatementChildrenHavingBase.childGetter(
        "variable_ref"
    )

    def computeStatement(self, constraint_collection):
        self.previous_trace = constraint_collection.onVariableDel(
            del_node = self
        )

        variable = self.getTargetVariableRef().getVariable()

        if self.isTolerant():
            if self.previous_trace.isUninitTrace():
                return (
                    None,
                    "new_statements",
                    "Removed tolerant 'del' statement of '%s' without effect." % (
                        variable.getName(),
                    )
                )


        # Any code could be run, note that.
        constraint_collection.onControlFlowEscape(self)

        # Need to fetch the potentially invalidated variable. A "del" on a
        # or shared value, may easily assign the global variable in "__del__".
        self.variable_trace = constraint_collection.getVariableCurrentTrace(
            variable = variable
        )

        return self, None, None

    def mayHaveSideEffects(self):
        return True

    def mayRaiseException(self, exception_type):
        if self.tolerant:
            return False
        else:
            if self.variable_trace is not None:

                variable = self.getTargetVariableRef().getVariable()
                # TODO: This condition must become unnecessary, but enhancing
                # SSA to notice potential escapes.
                if not variable.isSharedTechnically():

                    # Temporary variables deletions won't raise, just because we don't
                    # create them that way. We can avoid going through SSA in these
                    # cases.
                    if variable.isTempVariable():
                        return False

                    # If SSA knows, that's fine.
                    if self.previous_trace is not None and \
                       self.previous_trace.mustHaveValue():
                        return False

            return True


class StatementReleaseVariable(NodeBase):
    kind = "STATEMENT_RELEASE_VARIABLE"

    def __init__(self, variable, source_ref):
        assert variable is not None, source_ref

        NodeBase.__init__(
            self,
            source_ref = source_ref
        )

        self.variable = variable

        self.variable_trace = None

    def getDetail(self):
        return "of variable %s" % self.variable

    def getDetails(self):
        return {
            "variable" : self.variable
        }

    def getDetailsForDisplay(self):
        if self.variable.getOwner() is not self.getParentVariableProvider():
            return {
                "variable" : self.variable.getName(),
                "owner"    : self.variable.getOwner().getCodeName()
            }
        else:
            return {
                "variable" : self.variable.getName(),
            }


    def getVariable(self):
        return self.variable

    def setVariable(self, variable):
        self.variable = variable

    def computeStatement(self, constraint_collection):
        self.variable_trace = constraint_collection.onVariableRelease(
            variable = self.variable
        )

        if self.variable_trace.isUninitTrace():
            return (
                None,
                "new_statements",
                "Uninitialized variable '%s' is not released." % (
                    self.variable.getName()
                )
            )

        # TODO: We might be able to remove ourselves based on the trace
        # we belong to.

        return self, None, None

    def mayHaveSideEffects(self):
        # May execute __del__ code, it would be sweet to be able to predict
        # that another reference will still be active for a value though.
        return True

    def mayRaiseException(self, exception_type):
        # By default, __del__ is not allowed to raise an exception.
        return False


class StatementDelAttribute(StatementChildrenHavingBase):
    kind = "STATEMENT_DEL_ATTRIBUTE"

    named_children = (
        "expression",
    )

    def __init__(self, expression, attribute_name, source_ref):
        StatementChildrenHavingBase.__init__(
            self,
            values     = {
                "expression" : expression
            },
            source_ref = source_ref
        )

        self.attribute_name = attribute_name

    def getDetails(self):
        return {
            "attribute_name" : self.attribute_name
        }

    def getDetail(self):
        return "to attribute %s" % self.attribute_name

    def getAttributeName(self):
        return self.attribute_name

    def setAttributeName(self, attribute_name):
        self.attribute_name = attribute_name

    getLookupSource = StatementChildrenHavingBase.childGetter("expression")

    def computeStatement(self, constraint_collection):
        constraint_collection.onExpression(self.getLookupSource())
        lookup_source = self.getLookupSource()

        if lookup_source.willRaiseException(BaseException):
            return makeStatementExpressionOnlyReplacementNode(
                expression = lookup_source,
                node       = self
            )

        # Any code could be run, note that.
        constraint_collection.onControlFlowEscape(self)

        return self, None, None


class StatementDelSubscript(StatementChildrenHavingBase):
    kind = "STATEMENT_DEL_SUBSCRIPT"

    named_children = (
        "expression",
        "subscript"
    )

    def __init__(self, expression, subscript, source_ref):
        StatementChildrenHavingBase.__init__(
            self,
            values     = {
                "expression" : expression,
                "subscript"  : subscript
            },
            source_ref = source_ref
        )

    getSubscribed = StatementChildrenHavingBase.childGetter("expression")
    getSubscript = StatementChildrenHavingBase.childGetter("subscript")

    def computeStatement(self, constraint_collection):
        constraint_collection.onExpression(self.getSubscribed())
        subscribed = self.getSubscribed()

        if subscribed.willRaiseException(BaseException):
            result = makeStatementExpressionOnlyReplacementNode(
                expression = subscribed,
                node       = self
            )

            return result, "new_raise", """\
Subscript 'del' raises exception in subscribed value, removed del."""

        constraint_collection.onExpression(self.getSubscript())
        subscript = self.getSubscript()

        if subscript.willRaiseException(BaseException):
            result = makeStatementOnlyNodesFromExpressions(
                expressions = (
                    subscribed,
                    subscript
                )
            )

            return result, "new_raise", """\
Subscript 'del' raises exception in subscript value, removed del."""

        # Any code could be run, note that.
        constraint_collection.onControlFlowEscape(self)

        return self, None, None


class StatementDelSlice(StatementChildrenHavingBase):
    kind = "STATEMENT_DEL_SLICE"

    named_children = (
        "expression",
        "lower",
        "upper"
    )

    def __init__(self, expression, lower, upper, source_ref):
        StatementChildrenHavingBase.__init__(
            self,
            values     = {
                "expression" : expression,
                "lower"      : lower,
                "upper"      : upper
            },
            source_ref = source_ref
        )

    getLookupSource = StatementChildrenHavingBase.childGetter("expression")
    getLower = StatementChildrenHavingBase.childGetter("lower")
    getUpper = StatementChildrenHavingBase.childGetter("upper")

    def computeStatement(self, constraint_collection):
        constraint_collection.onExpression(self.getLookupSource())
        lookup_source = self.getLookupSource()

        if lookup_source.willRaiseException(BaseException):
            result = makeStatementExpressionOnlyReplacementNode(
                expression = lookup_source,
                node       = self
            )

            return result, "new_raise", """\
Slice del raises exception in sliced value, removed del"""


        constraint_collection.onExpression(self.getLower(), allow_none = True)
        lower = self.getLower()

        if lower is not None and lower.willRaiseException(BaseException):
            result = makeStatementOnlyNodesFromExpressions(
                expressions = (
                    lookup_source,
                    lower
                )
            )

            return result, "new_raise", """
Slice del raises exception in lower slice boundary value, removed del"""

        constraint_collection.onExpression(self.getUpper(), allow_none = True)
        upper = self.getUpper()

        if upper is not None and upper.willRaiseException(BaseException):
            result = makeStatementOnlyNodesFromExpressions(
                expressions = (
                    lookup_source,
                    lower,
                    upper
                )
            )

            return result, "new_raise", """
Slice del raises exception in upper slice boundary value, removed del"""

        # Any code could be run, note that.
        constraint_collection.onControlFlowEscape(self)

        return self, None, None
